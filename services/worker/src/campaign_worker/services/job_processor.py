from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import (
    CampaignStatus,
    ErrorComponent,
    RevisionTarget,
    SQSOperation,
    StepStatus,
    WorkflowStep,
)
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.sqs import SQSJobMessage
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.graph import nodes
from campaign_worker.graph.boundary import NodeFailure
from campaign_worker.graph.executor import _CompiledGraph, build_resume_graph, build_start_graph
from campaign_worker.graph.state import GraphState
from campaign_worker.providers.base import ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.repositories.workflow_repository import LeaseContext, WorkflowRepository

# Pipeline order used only to decide which steps a REGENERATE message seeds as REUSED
# before build_start_graph runs: every step strictly before message.requested_step.
_PIPELINE_ORDER: tuple[WorkflowStep, ...] = (
    WorkflowStep.STRATEGY,
    WorkflowStep.COPY,
    WorkflowStep.STORYBOARD,
    WorkflowStep.IMAGES,
    WorkflowStep.VIDEO,
)


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    completed: bool
    marker: str


class JobProcessor(ABC):
    @abstractmethod
    async def process(
        self, message: SQSJobMessage, version: CampaignVersion, lease: LeaseContext
    ) -> ProcessingResult: ...


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
        image_provider: ImageProvider,
        voice_provider: VoiceProvider,
        video_provider: VideoProvider,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._repository = repository
        self._image_provider = image_provider
        self._voice_provider = voice_provider
        self._video_provider = video_provider
        self._is_cancelled = is_cancelled or _never_cancelled

    async def process(self, message: SQSJobMessage, version: CampaignVersion, lease: LeaseContext) -> ProcessingResult:
        if message.operation == SQSOperation.REGENERATE:
            if message.revision_scope == RevisionTarget.SELECTED_IMAGES:
                return await self._fail_unsupported_scope(version, lease)
            # Seeding must complete before build_start_graph runs, and any seeding
            # failure must propagate (not be swallowed) so processing never silently
            # continues into provider execution for a step that was never actually
            # marked reused.
            await self._seed_reused_steps(version, message)

        graph = self._build_graph(message.operation)
        current = version
        try:
            async for chunk in graph.astream({"version": current}, stream_mode="values"):
                current = chunk["version"]
                await self._repository.save_version(current, lease)
            return ProcessingResult(completed=True, marker=f"{message.operation.value}_COMPLETED")
        except BaseException as exc:
            step, error = (exc.step, exc.error) if isinstance(exc, NodeFailure) else (None, exc)
            return await self._fail(current, error, lease, step=step)

    def _build_graph(self, operation: SQSOperation) -> _CompiledGraph:
        if operation in (SQSOperation.START, SQSOperation.REGENERATE):
            return build_start_graph(
                self._repository,
                self._is_cancelled,
                self._image_provider,
                self._voice_provider,
                self._video_provider,
            )
        return build_resume_graph(self._is_cancelled)

    async def _seed_reused_steps(self, version: CampaignVersion, message: SQSJobMessage) -> None:
        requested_step = message.requested_step
        if requested_step is None or requested_step not in _PIPELINE_ORDER:
            return
        now = datetime.now(UTC)
        for step in _PIPELINE_ORDER[: _PIPELINE_ORDER.index(requested_step)]:
            await self._repository.save_step(
                WorkflowStepRecord(
                    campaign_id=version.campaign_id,
                    campaign_version=version.campaign_version,
                    step=step,
                    status=StepStatus.REUSED,
                    created_at=now,
                    updated_at=now,
                )
            )

    async def _fail_unsupported_scope(self, version: CampaignVersion, lease: LeaseContext) -> ProcessingResult:
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
        await self._repository.save_version(updated_version, lease)
        return ProcessingResult(completed=True, marker="FAILURE_RECORDED")

    async def _fail(
        self, version: CampaignVersion, error: BaseException, lease: LeaseContext, *, step: WorkflowStep | None
    ) -> ProcessingResult:
        state: GraphState = {"version": version}
        failed_state = await nodes.handle_failure(state, error, step=step)
        await self._repository.save_version(failed_state["version"], lease)
        return ProcessingResult(completed=True, marker="FAILURE_RECORDED")
