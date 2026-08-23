from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import (
    Actor,
    CampaignEventType,
    CampaignStatus,
    ErrorComponent,
    RevisionTarget,
    SQSOperation,
    StepStatus,
    WorkflowStep,
)
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.events import CampaignEvent
from campaign_contracts.sqs import SQSJobMessage
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.audio.pipeline import VoiceAssetPipeline
from campaign_worker.errors import WorkflowOperationError
from campaign_worker.events import deterministic_event_id
from campaign_worker.graph import nodes
from campaign_worker.graph.boundary import NodeFailure
from campaign_worker.graph.executor import _CompiledGraph, build_resume_graph, build_start_graph
from campaign_worker.graph.state import GraphState
from campaign_worker.images.pipeline import ImageAssetPipeline
from campaign_worker.package.pipeline import PackageAssetPipeline
from campaign_worker.providers.base import CreativePlanProvider, ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.providers.mock_package_pipeline import MockPackagePipeline
from campaign_worker.repositories.workflow_repository import LeaseContext, WorkflowRepository
from campaign_worker.video.pipeline import VideoAssetPipeline

from ..graph.creative_plan_provider import DeterministicCreativePlanProvider

# Pipeline order used only to decide which steps a REGENERATE message seeds as REUSED
# before build_start_graph runs: every step strictly before message.requested_step.
_PIPELINE_ORDER: tuple[WorkflowStep, ...] = (
    WorkflowStep.STRATEGY,
    WorkflowStep.COPY,
    WorkflowStep.STORYBOARD,
    WorkflowStep.CREATIVE_PLAN,
    WorkflowStep.IMAGES,
    WorkflowStep.VOICEOVER,
    WorkflowStep.VIDEO,
)

_TERMINAL_STEP_STATUSES = (StepStatus.SUCCEEDED, StepStatus.REUSED, StepStatus.SKIPPED)


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    completed: bool
    marker: str


class JobProcessor(ABC):
    @abstractmethod
    async def process(
        self, message: SQSJobMessage, version: CampaignVersion, lease: LeaseContext
    ) -> ProcessingResult: ...

    async def fail_delivery_exhausted(
        self, version: CampaignVersion, lease: LeaseContext, correlation_id: UUID
    ) -> ProcessingResult | None:
        """No-op by default; only GraphJobProcessor persists a terminal FAILED state here."""
        return None


class NoOpJobProcessor(JobProcessor):
    """Task 9 boundary only: records acceptance without executing workflow nodes."""

    async def process(self, message: SQSJobMessage, version: CampaignVersion, lease: LeaseContext) -> ProcessingResult:
        return ProcessingResult(completed=True, marker="NO_OP_CHECKPOINT_COMMITTED")


async def _never_cancelled() -> bool:
    return False


class GraphJobProcessor(JobProcessor):
    """Runs the stateless LangGraph workflow for a single SQS message (ADR-001: no checkpointer).

    START runs the full pipeline through await_human_approval; RESUME runs only
    prepare_final_package. Failures (including cancellation) are routed through
    nodes.handle_failure and persisted like any other outcome -- there is no
    LangGraph-native exception routing in this design.
    """

    def __init__(
        self,
        repository: WorkflowRepository,
        image_provider: ImageProvider | ImageAssetPipeline,
        voice_provider: VoiceProvider | VoiceAssetPipeline,
        video_provider: VideoProvider | VideoAssetPipeline,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
        package_pipeline: PackageAssetPipeline | None = None,
        creative_plan_provider: CreativePlanProvider | None = None,
    ) -> None:
        self._repository = repository
        self._image_provider = image_provider
        self._voice_provider = voice_provider
        self._video_provider = video_provider
        self._is_cancelled = is_cancelled or _never_cancelled
        self._package_pipeline = package_pipeline or MockPackagePipeline()
        self._creative_plan_provider = creative_plan_provider or DeterministicCreativePlanProvider()

    async def process(self, message: SQSJobMessage, version: CampaignVersion, lease: LeaseContext) -> ProcessingResult:
        if message.operation == SQSOperation.REGENERATE:
            if message.revision_scope == RevisionTarget.SELECTED_IMAGES:
                return await self._fail_unsupported_scope(version, lease, message.correlation_id)
            # Seeding must complete before build_start_graph runs, and any seeding
            # failure must propagate (not be swallowed) so processing never silently
            # continues into provider execution for a step that was never actually
            # marked reused.
            await self._seed_reused_steps(version, message)

        graph = self._build_graph(message.operation)
        current = version
        starting_status = version.status
        try:
            async for chunk in graph.astream(
                {"version": current, "correlation_id": message.correlation_id}, stream_mode="values"
            ):
                current = chunk["version"]
                events = self._terminal_events(current, starting_status, message.correlation_id)
                await self._repository.save_version(current, lease, events)
            return ProcessingResult(completed=True, marker=f"{message.operation.value}_COMPLETED")
        except BaseException as exc:
            step, error = (exc.step, exc.error) if isinstance(exc, NodeFailure) else (None, exc)
            if step is None:
                # A failure with no NodeFailure attribution happened between graph
                # nodes -- e.g. this loop's own save_version() call above, not inside
                # a with_failure_attribution-wrapped node -- so there is no step whose
                # own execution failed. Derive the safe resume point from the durable
                # per-step records with_step_tracking already writes, instead of
                # leaving resume_step unset (see _last_completed_pipeline_step).
                step = await self._last_completed_pipeline_step(current)
            return await self._fail(current, error, lease, step=step, correlation_id=message.correlation_id)

    async def _last_completed_pipeline_step(self, version: CampaignVersion) -> WorkflowStep | None:
        """The last step in _PIPELINE_ORDER with a terminal (SUCCEEDED/REUSED/SKIPPED)
        WorkflowStepRecord -- i.e. the step whose output the failed save_version call
        was trying to persist. Retry can safely treat that step as done: with_step_
        tracking's own SUCCEEDED/REUSED/SKIPPED short-circuit (graph/boundary.py)
        guarantees it will not be re-executed, so this never causes duplicate work
        (e.g. a re-render of an already-uploaded video). Returns None only if no
        pipeline step has completed at all, in which case there is genuinely nothing
        to resume from and retry should stay blocked -- this must never be defaulted
        to a fixed step.
        """
        last: WorkflowStep | None = None
        for step in _PIPELINE_ORDER:
            record = await self._repository.get_step(version.campaign_id, version.campaign_version, step)
            if record is not None and record.status in _TERMINAL_STEP_STATUSES:
                last = step
        return last

    async def _in_flight_step(self, version: CampaignVersion) -> WorkflowStep | None:
        """The step that was actively executing when delivery attempts ran out: the one
        immediately after the last step with a terminal (SUCCEEDED/REUSED/SKIPPED) record.
        Falls back to the previously recorded resume_step if no pipeline step has completed
        at all, so a campaign that never got past its first step still gets attributed.
        """
        last_completed = await self._last_completed_pipeline_step(version)
        if last_completed is None:
            return version.retry.resume_step or _PIPELINE_ORDER[0]
        index = _PIPELINE_ORDER.index(last_completed)
        if index + 1 < len(_PIPELINE_ORDER):
            return _PIPELINE_ORDER[index + 1]
        return last_completed

    async def fail_delivery_exhausted(
        self, version: CampaignVersion, lease: LeaseContext, correlation_id: UUID
    ) -> ProcessingResult | None:
        """Last-resort terminal handling for a message whose SQS delivery attempts ran out
        (see SQSConsumer.process_raw's exhaustion branch). Without this, a job can be
        redriven to the DLQ while its campaign is left exactly as it was found -- QUEUED with
        error=None -- because record_exhausted() only writes an unrelated DynamoDB marker
        item. Delegates to the same handle_failure/_fail path normal in-graph failures use,
        so the persisted shape (status, error, retry.resume_step, FAILED event) is identical.
        """
        if version.status in (CampaignStatus.FAILED, CampaignStatus.CANCELLED, CampaignStatus.FINAL):
            return None
        step = await self._in_flight_step(version)
        error = WorkflowOperationError(
            code="DELIVERY_RETRY_EXHAUSTED",
            message="SQS delivery attempts exhausted before the workflow could complete or fail cleanly",
            retryable=False,
        )
        return await self._fail(version, error, lease, step=step, correlation_id=correlation_id)

    def _terminal_events(
        self, version: CampaignVersion, starting_status: CampaignStatus, correlation_id: UUID
    ) -> list[CampaignEvent]:
        # Fires only on the specific streamed chunk where the version first reaches a
        # terminal milestone during THIS processing attempt -- comparing against the
        # status this attempt started from (not just the new value) means a redelivery
        # that re-runs an untracked node (await_human_approval/prepare_final_package)
        # after it already reached this status in a prior attempt does not re-attach the
        # event. The deterministic event_id in _build_terminal_event is a second,
        # transaction-level backstop for the same guarantee.
        if version.status == CampaignStatus.READY_FOR_REVIEW and starting_status != CampaignStatus.READY_FOR_REVIEW:
            return [self._build_terminal_event(version, CampaignEventType.REVIEW_READY, correlation_id)]
        if version.status == CampaignStatus.FINAL and starting_status != CampaignStatus.FINAL:
            return [self._build_terminal_event(version, CampaignEventType.FINALIZED, correlation_id)]
        return []

    @staticmethod
    def _build_terminal_event(
        version: CampaignVersion, event_type: CampaignEventType, correlation_id: UUID
    ) -> CampaignEvent:
        return CampaignEvent(
            event_id=deterministic_event_id(
                version.campaign_id, version.campaign_version, event_type, version.status.value
            ),
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            event_sequence=1,  # placeholder -- the repository assigns the real value transactionally
            event_type=event_type,
            status=version.status,
            progress_percent=version.progress_percent,
            occurred_at=datetime.now(UTC),
            actor=Actor.LANGGRAPH_WORKER,
            correlation_id=correlation_id,
            job_id=version.job_id,
        )

    def _build_graph(self, operation: SQSOperation) -> _CompiledGraph:
        if operation in (SQSOperation.START, SQSOperation.REGENERATE):
            return build_start_graph(
                self._repository,
                self._is_cancelled,
                self._image_provider,
                self._voice_provider,
                self._video_provider,
                creative_plan_provider=self._creative_plan_provider,
            )
        return build_resume_graph(self._repository, self._is_cancelled, self._package_pipeline)

    async def _seed_reused_steps(self, version: CampaignVersion, message: SQSJobMessage) -> None:
        requested_step = message.requested_step
        if requested_step is None or requested_step not in _PIPELINE_ORDER:
            return
        now = datetime.now(UTC)
        for step in _PIPELINE_ORDER[: _PIPELINE_ORDER.index(requested_step)]:
            event = CampaignEvent(
                event_id=deterministic_event_id(
                    version.campaign_id, version.campaign_version, CampaignEventType.STEP_REUSED, step.value
                ),
                campaign_id=version.campaign_id,
                campaign_version=version.campaign_version,
                event_sequence=1,  # placeholder -- the repository assigns the real value transactionally
                event_type=CampaignEventType.STEP_REUSED,
                status=version.status,
                step=step,
                progress_percent=version.progress_percent,
                occurred_at=now,
                actor=Actor.LANGGRAPH_WORKER,
                correlation_id=message.correlation_id,
                job_id=version.job_id,
            )
            await self._repository.save_step(
                WorkflowStepRecord(
                    campaign_id=version.campaign_id,
                    campaign_version=version.campaign_version,
                    step=step,
                    status=StepStatus.REUSED,
                    created_at=now,
                    updated_at=now,
                ),
                [event],
            )

    async def _fail_unsupported_scope(
        self, version: CampaignVersion, lease: LeaseContext, correlation_id: UUID
    ) -> ProcessingResult:
        now = datetime.now(UTC)
        error = SanitizedWorkflowError(
            code="INTERNAL_ERROR",
            message="SELECTED_IMAGES partial regeneration is not yet supported",
            component=ErrorComponent.LANGGRAPH_WORKER,
            workflow_step=WorkflowStep.IMAGES,
            attempt=max(version.retry.attempt, 1),
            retryable=False,
            timestamp=now,
            correlation_id=uuid4(),
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            job_id=version.job_id,
        )
        updated_version = version.model_copy(
            update={
                "status": CampaignStatus.FAILED,
                "error": error,
                "retry": version.retry.model_copy(update={"retryable": False}),
            }
        )
        event = self._failure_event(updated_version, correlation_id, occurred_at=now)
        await self._repository.save_version(updated_version, lease, [event])
        return ProcessingResult(completed=True, marker="FAILURE_RECORDED")

    async def _fail(
        self,
        version: CampaignVersion,
        error: BaseException,
        lease: LeaseContext,
        *,
        step: WorkflowStep | None,
        correlation_id: UUID,
    ) -> ProcessingResult:
        state: GraphState = {"version": version}
        failed_state = await nodes.handle_failure(state, error, step=step)
        updated_version = failed_state["version"]
        event = self._failure_event(updated_version, correlation_id, occurred_at=datetime.now(UTC))
        await self._repository.save_version(updated_version, lease, [event])
        return ProcessingResult(completed=True, marker="FAILURE_RECORDED")

    @staticmethod
    def _failure_event(
        updated_version: CampaignVersion, correlation_id: UUID, *, occurred_at: datetime
    ) -> CampaignEvent:
        # handle_failure (graph/nodes.py) is the only place that ever sets FAILED or, via
        # its NodeCancelled branch, CANCELLED -- so updated_version.status fully
        # determines which semantic event this transition durably represents.
        if updated_version.status == CampaignStatus.CANCELLED:
            event_type, discriminator = CampaignEventType.CANCELLED, "CANCELLED"
        else:
            event_type, discriminator = CampaignEventType.FAILED, str(updated_version.retry.attempt)
        error = updated_version.error
        details: dict[str, object] = {} if error is None else {"code": error.code, "retryable": error.retryable}
        return CampaignEvent(
            event_id=deterministic_event_id(
                updated_version.campaign_id, updated_version.campaign_version, event_type, discriminator
            ),
            campaign_id=updated_version.campaign_id,
            campaign_version=updated_version.campaign_version,
            event_sequence=1,  # placeholder -- the repository assigns the real value transactionally
            event_type=event_type,
            status=updated_version.status,
            step=error.workflow_step if error is not None else None,
            progress_percent=updated_version.progress_percent,
            occurred_at=occurred_at,
            actor=Actor.LANGGRAPH_WORKER,
            correlation_id=correlation_id,
            job_id=updated_version.job_id,
            details=details,
        )
