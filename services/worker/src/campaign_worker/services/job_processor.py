from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import SQSOperation, WorkflowStep
from campaign_contracts.sqs import SQSJobMessage

from campaign_worker.graph import nodes
from campaign_worker.graph.boundary import NodeFailure
from campaign_worker.graph.executor import _CompiledGraph, build_resume_graph, build_start_graph
from campaign_worker.graph.state import GraphState
from campaign_worker.providers.base import ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.repositories.workflow_repository import LeaseContext, WorkflowRepository


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
            return await self._fail(version, ValueError("REGENERATE is not yet supported"), lease, step=None)

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
        if operation == SQSOperation.START:
            return build_start_graph(
                self._repository,
                self._is_cancelled,
                self._image_provider,
                self._voice_provider,
                self._video_provider,
            )
        return build_resume_graph(self._is_cancelled)

    async def _fail(
        self, version: CampaignVersion, error: BaseException, lease: LeaseContext, *, step: WorkflowStep | None
    ) -> ProcessingResult:
        state: GraphState = {"version": version}
        failed_state = await nodes.handle_failure(state, error, step=step)
        await self._repository.save_version(failed_state["version"], lease)
        return ProcessingResult(completed=True, marker="FAILURE_RECORDED")
