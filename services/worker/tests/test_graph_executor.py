from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus, StepStatus, WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.graph.executor import (
    GraphExecutor,
    build_default_graph,
    build_graph,
    build_resume_graph,
    build_start_graph,
)
from campaign_worker.graph.state import GraphState
from campaign_worker.providers.mock_image_provider import MockImageProvider
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.repositories.workflow_repository import WorkflowRepository


class _InMemoryStepRepository(WorkflowRepository):
    def __init__(self, seed: dict[tuple, WorkflowStepRecord] | None = None) -> None:
        self.steps: dict[tuple, WorkflowStepRecord] = dict(seed or {})

    async def get_step(self, campaign_id: UUID, campaign_version: int, step: WorkflowStep) -> WorkflowStepRecord | None:
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record: WorkflowStepRecord) -> None:
        self.steps[(record.campaign_id, record.campaign_version, record.step)] = record

    async def load_version(self, message):
        raise NotImplementedError

    async def acquire_lease(self, message, owner, now, expires_at):
        raise NotImplementedError

    async def heartbeat(self, message, lease, now, expires_at):
        raise NotImplementedError

    async def is_completed(self, message):
        raise NotImplementedError

    async def complete(self, message, lease, completed_at):
        raise NotImplementedError

    async def release(self, message, lease):
        raise NotImplementedError

    async def record_exhausted(self, message, receive_count, now):
        raise NotImplementedError

    async def record_invalid(self, campaign_id, code, message_id, now):
        raise NotImplementedError

    async def available(self):
        raise NotImplementedError

    async def save_version(self, version, lease):
        raise NotImplementedError


def _brief():
    return CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew",
        business_description="A local roaster offering weekly delivery.",
        campaign_goal="increase sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )


def _version(**overrides):
    now = datetime.now(UTC)
    defaults = dict(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=_brief(),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


def _step_record(campaign_id: UUID, step: WorkflowStep, status: StepStatus) -> WorkflowStepRecord:
    now = datetime.now(UTC)
    return WorkflowStepRecord(
        campaign_id=campaign_id, campaign_version=1, step=step, status=status, created_at=now, updated_at=now
    )


async def _never_cancelled() -> bool:
    return False


@pytest.mark.asyncio
async def test_executor_runs_graph_and_returns_updated_version():
    async def passthrough(state: GraphState) -> GraphState:
        return state

    graph = build_graph(nodes={"passthrough": passthrough}, edges=[("passthrough",)])
    executor = GraphExecutor(graph)
    version = _version()
    result = await executor.run(version)
    assert result.campaign_id == version.campaign_id


@pytest.mark.asyncio
async def test_executor_uses_no_checkpointer():
    async def noop(state: GraphState) -> GraphState:
        return state

    graph = build_graph(nodes={"noop": noop}, edges=[("noop",)])
    assert graph.checkpointer is None


@pytest.mark.asyncio
async def test_executor_propagates_node_failure():
    async def failing(state: GraphState) -> GraphState:
        raise ValueError("simulated invalid transition")

    graph = build_graph(nodes={"failing": failing}, edges=[("failing",)])
    executor = GraphExecutor(graph)
    with pytest.raises(ValueError, match="simulated invalid transition"):
        await executor.run(_version())


@pytest.mark.asyncio
async def test_executor_chains_multiple_nodes_in_order():
    calls: list[str] = []

    async def first(state: GraphState) -> GraphState:
        calls.append("first")
        return state

    async def second(state: GraphState) -> GraphState:
        calls.append("second")
        return state

    graph = build_graph(nodes={"first": first, "second": second}, edges=[("first",), ("second",)])
    executor = GraphExecutor(graph)
    await executor.run(_version())
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_default_graph_runs_all_six_nodes_end_to_end():
    graph = build_default_graph(_InMemoryStepRepository(), _never_cancelled)
    executor = GraphExecutor(graph)
    result = await executor.run(_version())
    assert result.strategy is not None
    assert result.campaign_copy is not None
    assert result.storyboard is not None


@pytest.mark.asyncio
async def test_default_graph_uses_no_checkpointer():
    graph = build_default_graph(_InMemoryStepRepository(), _never_cancelled)
    assert graph.checkpointer is None


@pytest.mark.asyncio
async def test_default_graph_first_run_executes_and_persists_every_tracked_step():
    repository = _InMemoryStepRepository()
    version = _version()
    graph = build_default_graph(repository, _never_cancelled)
    executor = GraphExecutor(graph)
    await executor.run(version)

    for step in (WorkflowStep.STRATEGY, WorkflowStep.COPY, WorkflowStep.STORYBOARD):
        record = repository.steps[(version.campaign_id, version.campaign_version, step)]
        assert record.status == StepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_default_graph_partial_completion_resumes_correctly():
    version = _version()
    sentinel_strategy = (
        await GraphExecutor(build_default_graph(_InMemoryStepRepository(), _never_cancelled)).run(version)
    ).strategy
    assert sentinel_strategy is not None

    # Simulate a reload after a crash: STRATEGY already succeeded and its output is
    # already on the version (as a real reload from persisted content would provide);
    # COPY and STORYBOARD have no STEP record yet.
    repository = _InMemoryStepRepository(
        seed={
            (version.campaign_id, 1, WorkflowStep.STRATEGY): _step_record(
                version.campaign_id, WorkflowStep.STRATEGY, StepStatus.SUCCEEDED
            )
        }
    )
    resumed_input = version.model_copy(update={"strategy": sentinel_strategy})
    graph = build_default_graph(repository, _never_cancelled)
    executor = GraphExecutor(graph)
    result = await executor.run(resumed_input)

    assert result.strategy == sentinel_strategy
    assert result.campaign_copy is not None
    assert result.storyboard is not None
    assert repository.steps[(version.campaign_id, 1, WorkflowStep.COPY)].status == StepStatus.SUCCEEDED
    assert repository.steps[(version.campaign_id, 1, WorkflowStep.STORYBOARD)].status == StepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_default_graph_rerun_skips_all_completed_steps():
    version = _version()
    repository = _InMemoryStepRepository()
    graph = build_default_graph(repository, _never_cancelled)
    executor = GraphExecutor(graph)
    first_result = await executor.run(version)

    # Rerun with the same repository (all three steps now SUCCEEDED) and the
    # first run's output already on the version, as a real reload would provide.
    rerun_input = version.model_copy(
        update={
            "strategy": first_result.strategy,
            "campaign_copy": first_result.campaign_copy,
            "storyboard": first_result.storyboard,
        }
    )
    second_result = await executor.run(rerun_input)

    assert second_result.strategy == first_result.strategy
    assert second_result.campaign_copy == first_result.campaign_copy
    assert second_result.storyboard == first_result.storyboard


@pytest.mark.asyncio
async def test_graph_execution_is_deterministic():
    version = _version()
    first = await GraphExecutor(build_default_graph(_InMemoryStepRepository(), _never_cancelled)).run(version)
    second = await GraphExecutor(build_default_graph(_InMemoryStepRepository(), _never_cancelled)).run(version)
    assert first == second


@pytest.mark.asyncio
async def test_default_graph_raises_node_cancelled_when_cancellation_flagged():
    from campaign_worker.graph.boundary import NodeCancelled

    async def always_cancelled() -> bool:
        return True

    graph = build_default_graph(_InMemoryStepRepository(), always_cancelled)
    executor = GraphExecutor(graph)
    with pytest.raises(NodeCancelled, match="receive_request"):
        await executor.run(_version())


@pytest.mark.asyncio
async def test_start_graph_runs_end_to_end_and_reaches_ready_for_review():
    graph = build_start_graph(
        _InMemoryStepRepository(), _never_cancelled, MockImageProvider(), MockVoiceProvider(), MockVideoProvider()
    )
    executor = GraphExecutor(graph)
    result = await executor.run(_version())

    assert result.status == CampaignStatus.READY_FOR_REVIEW
    assert result.strategy is not None
    assert result.campaign_copy is not None
    assert result.storyboard is not None
    assert len(result.image_artifacts) == 3
    assert result.video_artifact is not None


@pytest.mark.asyncio
async def test_start_graph_uses_no_checkpointer():
    graph = build_start_graph(
        _InMemoryStepRepository(), _never_cancelled, MockImageProvider(), MockVoiceProvider(), MockVideoProvider()
    )
    assert graph.checkpointer is None


@pytest.mark.asyncio
async def test_start_graph_persists_every_tracked_step():
    repository = _InMemoryStepRepository()
    version = _version()
    graph = build_start_graph(
        repository, _never_cancelled, MockImageProvider(), MockVoiceProvider(), MockVideoProvider()
    )
    await GraphExecutor(graph).run(version)

    for step in (
        WorkflowStep.STRATEGY,
        WorkflowStep.COPY,
        WorkflowStep.STORYBOARD,
        WorkflowStep.IMAGES,
        WorkflowStep.VIDEO,
    ):
        record = repository.steps[(version.campaign_id, version.campaign_version, step)]
        assert record.status == StepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_start_graph_raises_node_cancelled_when_cancellation_flagged():
    from campaign_worker.graph.boundary import NodeCancelled

    async def always_cancelled() -> bool:
        return True

    graph = build_start_graph(
        _InMemoryStepRepository(), always_cancelled, MockImageProvider(), MockVoiceProvider(), MockVideoProvider()
    )
    with pytest.raises(NodeCancelled, match="receive_request"):
        await GraphExecutor(graph).run(_version())


@pytest.mark.asyncio
async def test_resume_graph_runs_prepare_final_package_only():
    version = (
        await GraphExecutor(
            build_start_graph(
                _InMemoryStepRepository(),
                _never_cancelled,
                MockImageProvider(),
                MockVoiceProvider(),
                MockVideoProvider(),
            )
        ).run(_version())
    ).model_copy(update={"status": CampaignStatus.APPROVED})

    graph = build_resume_graph(_never_cancelled)
    result = await GraphExecutor(graph).run(version)

    assert result.status == CampaignStatus.FINAL
    assert result.review_package is not None


@pytest.mark.asyncio
async def test_resume_graph_uses_no_checkpointer():
    graph = build_resume_graph(_never_cancelled)
    assert graph.checkpointer is None
