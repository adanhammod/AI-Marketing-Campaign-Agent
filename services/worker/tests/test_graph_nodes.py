from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus

from campaign_worker.graph import nodes
from campaign_worker.graph.state import GraphState


def _version(**overrides):
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram", "facebook"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals aged 25-40",
        call_to_action="Subscribe today",
    )
    defaults = dict(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


@pytest.mark.asyncio
async def test_receive_request_and_analyze_campaign_are_passthrough_safe():
    state: GraphState = {"version": _version()}
    after_receive = await nodes.receive_request(state)
    after_analyze = await nodes.analyze_campaign(after_receive)
    assert after_analyze["version"].campaign_id == state["version"].campaign_id


@pytest.mark.asyncio
async def test_validate_input_rejects_blank_description():
    brief = _version().brief.model_copy(update={"business_description": "                    "})
    state: GraphState = {"version": _version(brief=brief)}
    with pytest.raises(ValueError, match="business_description"):
        await nodes.validate_input(state)


@pytest.mark.asyncio
async def test_validate_input_accepts_populated_description():
    state: GraphState = {"version": _version()}
    result = await nodes.validate_input(state)
    assert result["version"].campaign_id == state["version"].campaign_id


@pytest.mark.asyncio
async def test_create_strategy_produces_schema_valid_output():
    state: GraphState = {"version": _version()}
    result = await nodes.create_strategy(state)
    strategy = result["version"].strategy
    assert strategy is not None
    assert strategy.audience == "Urban professionals aged 25-40"
    assert "instagram" in strategy.channel_rationale
    assert "facebook" in strategy.channel_rationale


@pytest.mark.asyncio
async def test_create_strategy_falls_back_to_general_audience_when_unspecified():
    version = _version()
    brief = version.brief.model_copy(update={"target_audience": None})
    state: GraphState = {"version": version.model_copy(update={"brief": brief})}
    result = await nodes.create_strategy(state)
    assert result["version"].strategy.audience == "general audience"


@pytest.mark.asyncio
async def test_generate_copy_requires_prior_strategy():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="create_strategy"):
        await nodes.generate_copy(state)


@pytest.mark.asyncio
async def test_generate_copy_produces_schema_valid_output():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    result = await nodes.generate_copy(strategized)
    copy = result["version"].campaign_copy
    assert copy is not None
    assert copy.call_to_action == "Subscribe today"
    assert len(copy.channel_variants) == 2
    assert {variant.channel for variant in copy.channel_variants} == {"instagram", "facebook"}


@pytest.mark.asyncio
async def test_create_storyboard_requires_prior_strategy():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="create_strategy"):
        await nodes.create_storyboard(state)


@pytest.mark.asyncio
async def test_create_storyboard_produces_three_scenes_totaling_valid_duration():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    copied = await nodes.generate_copy(strategized)
    result = await nodes.create_storyboard(copied)
    storyboard = result["version"].storyboard
    assert storyboard is not None
    assert [s.scene_number for s in storyboard.scenes] == [1, 2, 3]
    assert storyboard.total_duration_seconds == sum(s.duration_seconds for s in storyboard.scenes)
    assert 13 <= storyboard.total_duration_seconds <= 17
