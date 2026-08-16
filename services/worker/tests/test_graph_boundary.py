from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, StrategyOutput
from campaign_contracts.enums import CampaignEventType, CampaignStatus, StepStatus, WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.graph.boundary import with_step_tracking
from campaign_worker.graph.state import GraphState
from campaign_worker.repositories.workflow_repository import WorkflowRepository


class FakeStepRepository(WorkflowRepository):
    def __init__(self, seed: dict[tuple, WorkflowStepRecord] | None = None) -> None:
        self.steps: dict[tuple, WorkflowStepRecord] = dict(seed or {})
        self.save_calls: list[WorkflowStepRecord] = []
        self.event_calls: list[list] = []

    async def get_step(self, campaign_id: UUID, campaign_version: int, step: WorkflowStep) -> WorkflowStepRecord | None:
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record: WorkflowStepRecord, events: list | None = None) -> None:
        self.save_calls.append(record)
        self.event_calls.append(events)
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

    async def save_version(self, version, lease, events=None):
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


def _step_record(
    campaign_id: UUID, status: StepStatus, step: WorkflowStep = WorkflowStep.STRATEGY
) -> WorkflowStepRecord:
    now = datetime.now(UTC)
    return WorkflowStepRecord(
        campaign_id=campaign_id,
        campaign_version=1,
        step=step,
        status=status,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_missing_step_executes_normally():
    repository = FakeStepRepository()
    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    version = _version()
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)
    await wrapped({"version": version})

    assert calls == ["ran"]
    saved_statuses = [record.status for record in repository.save_calls]
    assert saved_statuses == [StepStatus.RUNNING, StepStatus.SUCCEEDED]
    emitted_types = [event.event_type for events in repository.event_calls for event in events]
    assert emitted_types == [CampaignEventType.STEP_STARTED, CampaignEventType.STEP_COMPLETED]


@pytest.mark.asyncio
async def test_completion_event_is_not_recorded_when_succeeded_persistence_fails():
    class FailingCompletionRepository(FakeStepRepository):
        async def save_step(self, record: WorkflowStepRecord, events: list | None = None) -> None:
            if record.status == StepStatus.SUCCEEDED:
                raise RuntimeError("durable completion failed")
            await super().save_step(record, events)

    repository = FailingCompletionRepository()

    async def node(state: GraphState) -> GraphState:
        return state

    version = _version()
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)

    with pytest.raises(RuntimeError, match="durable completion failed"):
        await wrapped({"version": version})

    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING]
    assert [event.event_type for events in repository.event_calls for event in events] == [
        CampaignEventType.STEP_STARTED
    ]


@pytest.mark.asyncio
async def test_rerun_skips_completed_step():
    campaign_id = uuid4()
    repository = FakeStepRepository(
        seed={(campaign_id, 1, WorkflowStep.STRATEGY): _step_record(campaign_id, StepStatus.SUCCEEDED)}
    )
    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    version = _version(campaign_id=campaign_id)
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)
    await wrapped({"version": version})

    assert calls == []
    assert repository.save_calls == []
    assert repository.event_calls == []


@pytest.mark.asyncio
async def test_reused_status_also_skips():
    campaign_id = uuid4()
    repository = FakeStepRepository(
        seed={(campaign_id, 1, WorkflowStep.STRATEGY): _step_record(campaign_id, StepStatus.REUSED)}
    )
    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    version = _version(campaign_id=campaign_id)
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)
    await wrapped({"version": version})

    assert calls == []


@pytest.mark.asyncio
async def test_previously_skipped_status_also_short_circuits():
    campaign_id = uuid4()
    repository = FakeStepRepository(
        seed={(campaign_id, 1, WorkflowStep.VOICEOVER): _step_record(campaign_id, StepStatus.SKIPPED, WorkflowStep.VOICEOVER)}
    )
    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    version = _version(campaign_id=campaign_id)
    wrapped = with_step_tracking(WorkflowStep.VOICEOVER, repository)(node)
    await wrapped({"version": version})

    assert calls == []
    assert repository.save_calls == []


@pytest.mark.asyncio
async def test_failed_step_is_retried_not_skipped():
    campaign_id = uuid4()
    repository = FakeStepRepository(
        seed={(campaign_id, 1, WorkflowStep.STRATEGY): _step_record(campaign_id, StepStatus.FAILED)}
    )
    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    version = _version(campaign_id=campaign_id)
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)
    await wrapped({"version": version})

    assert calls == ["ran"]
    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING, StepStatus.SUCCEEDED]
    emitted_types = [event.event_type for events in repository.event_calls for event in events]
    assert emitted_types == [CampaignEventType.STEP_STARTED, CampaignEventType.STEP_COMPLETED]


@pytest.mark.asyncio
async def test_skipped_step_reuses_persisted_output_without_recomputing():
    campaign_id = uuid4()
    sentinel_strategy = StrategyOutput(
        audience="PRE-PERSISTED-SENTINEL",
        positioning="sentinel positioning",
        objective="sentinel objective",
        key_message="sentinel key message",
        channel_rationale={"instagram": "sentinel rationale"},
    )
    repository = FakeStepRepository(
        seed={(campaign_id, 1, WorkflowStep.STRATEGY): _step_record(campaign_id, StepStatus.SUCCEEDED)}
    )

    async def recompute_differently(state: GraphState) -> GraphState:
        version = state["version"]
        different = StrategyOutput(
            audience="FRESHLY-RECOMPUTED",
            positioning="x",
            objective="y",
            key_message="z",
            channel_rationale={},
        )
        return {"version": version.model_copy(update={"strategy": different})}

    version = _version(campaign_id=campaign_id, strategy=sentinel_strategy)
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(recompute_differently)
    result = await wrapped({"version": version})

    assert result["version"].strategy == sentinel_strategy
    assert result["version"].strategy.audience == "PRE-PERSISTED-SENTINEL"


@pytest.mark.asyncio
async def test_skip_sentinel_records_skipped_status_and_emits_step_skipped_event():
    repository = FakeStepRepository()

    async def node(state: GraphState) -> GraphState:
        return {"version": state["version"], "_step_skipped": True, "_skip_reason": "video_style=CINEMATIC_TEXT_AD"}

    version = _version()
    wrapped = with_step_tracking(WorkflowStep.VOICEOVER, repository)(node)
    await wrapped({"version": version})

    saved_statuses = [record.status for record in repository.save_calls]
    assert saved_statuses == [StepStatus.RUNNING, StepStatus.SKIPPED]
    emitted = [event for events in repository.event_calls for event in events]
    assert [event.event_type for event in emitted] == [CampaignEventType.STEP_STARTED, CampaignEventType.STEP_SKIPPED]
    assert emitted[1].details == {"reason": "video_style=CINEMATIC_TEXT_AD"}


@pytest.mark.asyncio
async def test_skip_sentinel_keys_are_stripped_from_returned_state():
    repository = FakeStepRepository()

    async def node(state: GraphState) -> GraphState:
        return {"version": state["version"], "_step_skipped": True, "_skip_reason": "because"}

    result = await with_step_tracking(WorkflowStep.VOICEOVER, repository)(node)({"version": _version()})

    assert "_step_skipped" not in result
    assert "_skip_reason" not in result


@pytest.mark.asyncio
async def test_skip_sentinel_without_reason_emits_empty_details():
    repository = FakeStepRepository()

    async def node(state: GraphState) -> GraphState:
        return {"version": state["version"], "_step_skipped": True}

    await with_step_tracking(WorkflowStep.VOICEOVER, repository)(node)({"version": _version()})

    emitted = [event for events in repository.event_calls for event in events]
    assert emitted[1].details == {}


@pytest.mark.asyncio
async def test_cancellation_check_raises_before_running_node():
    from campaign_worker.graph.boundary import NodeCancelled, with_cancellation_check

    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    async def is_cancelled() -> bool:
        return True

    wrapped = with_cancellation_check(is_cancelled, "create_strategy")(node)
    with pytest.raises(NodeCancelled, match="create_strategy"):
        await wrapped({"version": _version()})

    assert calls == []


@pytest.mark.asyncio
async def test_cancellation_check_runs_node_when_not_cancelled():
    from campaign_worker.graph.boundary import with_cancellation_check

    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    async def is_cancelled() -> bool:
        return False

    wrapped = with_cancellation_check(is_cancelled, "create_strategy")(node)
    await wrapped({"version": _version()})

    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_cancellation_check_composes_with_step_tracking():
    from campaign_worker.graph.boundary import with_cancellation_check

    repository = FakeStepRepository()
    calls: list[str] = []

    async def node(state: GraphState) -> GraphState:
        calls.append("ran")
        return state

    async def is_cancelled() -> bool:
        return True

    tracked = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)
    wrapped = with_cancellation_check(is_cancelled, "create_strategy")(tracked)

    with pytest.raises(Exception, match="create_strategy"):
        await wrapped({"version": _version()})

    assert calls == []
    assert repository.save_calls == []


@pytest.mark.asyncio
async def test_with_failure_attribution_tags_a_generic_exception_with_the_given_step():
    from campaign_worker.graph.boundary import NodeFailure, with_failure_attribution

    async def failing(state: GraphState) -> GraphState:
        raise ValueError("boom")

    wrapped = with_failure_attribution(WorkflowStep.STRATEGY)(failing)

    with pytest.raises(NodeFailure) as exc_info:
        await wrapped({"version": _version()})

    assert exc_info.value.step == WorkflowStep.STRATEGY
    assert isinstance(exc_info.value.error, ValueError)
    assert str(exc_info.value.error) == "boom"


@pytest.mark.asyncio
async def test_with_failure_attribution_preserves_node_cancelled_as_the_wrapped_error():
    from campaign_worker.graph.boundary import (
        NodeCancelled,
        NodeFailure,
        with_cancellation_check,
        with_failure_attribution,
    )

    async def node(state: GraphState) -> GraphState:
        return state

    async def is_cancelled() -> bool:
        return True

    cancellable = with_cancellation_check(is_cancelled, "create_strategy")(node)
    wrapped = with_failure_attribution(WorkflowStep.STRATEGY)(cancellable)

    with pytest.raises(NodeFailure) as exc_info:
        await wrapped({"version": _version()})

    assert exc_info.value.step == WorkflowStep.STRATEGY
    assert isinstance(exc_info.value.error, NodeCancelled)


@pytest.mark.asyncio
async def test_with_failure_attribution_does_not_interfere_with_successful_nodes():
    from campaign_worker.graph.boundary import with_failure_attribution

    async def node(state: GraphState) -> GraphState:
        return {"version": state["version"]}

    wrapped = with_failure_attribution(WorkflowStep.STRATEGY)(node)
    version = _version()
    result = await wrapped({"version": version})

    assert result["version"] is version
