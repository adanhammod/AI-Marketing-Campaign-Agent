from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus

from campaign_worker.graph.executor import GraphExecutor, build_graph
from campaign_worker.graph.state import GraphState


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


def _version():
    now = datetime.now(UTC)
    return CampaignVersion(
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
