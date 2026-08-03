from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus

from campaign_marketing_mcp.service import CampaignNotFound, DuplicateCampaign, MarketingMCPService


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


def _records():
    now = datetime.now(UTC)
    campaign_id = uuid4()
    aggregate = CampaignAggregateMetadata(
        campaign_id=campaign_id,
        current_version=1,
        title="Example Coffee",
        created_at=now,
        updated_at=now,
        lock_version=0,
    )
    version = CampaignVersion(
        campaign_id=campaign_id,
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.CREATED,
        progress_percent=0,
        brief=_brief(),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=0,
    )
    return aggregate, version


@pytest.mark.asyncio
async def test_create_campaign_is_idempotent_for_same_key():
    service = MarketingMCPService()
    aggregate, version = _records()
    first = await service.create_campaign(aggregate, version, idempotency_key="key-1")
    second = await service.create_campaign(aggregate, version, idempotency_key="key-1")
    assert first == second


@pytest.mark.asyncio
async def test_create_campaign_conflicts_on_different_key_same_id():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    with pytest.raises(DuplicateCampaign):
        await service.create_campaign(aggregate, version, idempotency_key="key-2")


@pytest.mark.asyncio
async def test_get_campaign_returns_none_when_absent():
    service = MarketingMCPService()
    assert await service.get_campaign(uuid4()) is None


@pytest.mark.asyncio
async def test_get_campaign_returns_created_record():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    fetched = await service.get_campaign(aggregate.campaign_id)
    assert fetched == (aggregate, version)


@pytest.mark.asyncio
async def test_save_campaign_content_updates_strategy_field():
    from campaign_contracts.campaign import StrategyOutput

    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    strategy = StrategyOutput(
        audience="general audience", positioning="x", objective="y", key_message="z", channel_rationale={}
    )
    updated = await service.save_campaign_content(
        version.campaign_id, version.campaign_version, "strategy", strategy, idempotency_key="content-1"
    )
    assert updated.strategy == strategy


@pytest.mark.asyncio
async def test_save_campaign_content_raises_for_unknown_campaign():
    service = MarketingMCPService()
    with pytest.raises(CampaignNotFound):
        await service.save_campaign_content(uuid4(), 1, "strategy", None, idempotency_key="content-1")


@pytest.mark.asyncio
async def test_update_campaign_status_transitions_and_records():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    updated = await service.update_campaign_status(
        version.campaign_id, version.campaign_version, CampaignStatus.QUEUED, idempotency_key="status-1"
    )
    assert updated.status == CampaignStatus.QUEUED


@pytest.mark.asyncio
async def test_update_campaign_applies_patch():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    updated = await service.update_campaign(version.campaign_id, {"progress_percent": 42}, idempotency_key="patch-1")
    assert updated.progress_percent == 42


@pytest.mark.asyncio
async def test_save_asset_metadata_records_artifact():
    service = MarketingMCPService()
    artifact = object()
    await service.save_asset_metadata(artifact)
    assert artifact in service._assets


@pytest.mark.asyncio
async def test_validate_campaign_package_false_until_content_present():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    assert await service.validate_campaign_package(version.campaign_id) is False


@pytest.mark.asyncio
async def test_validate_campaign_package_true_when_content_complete():
    from campaign_contracts.campaign import CampaignCopy, Storyboard, StoryboardScene, StrategyOutput

    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    strategy = StrategyOutput(audience="a", positioning="p", objective="o", key_message="k", channel_rationale={})
    await service.save_campaign_content(version.campaign_id, 1, "strategy", strategy, idempotency_key="c1")
    copy = CampaignCopy(headline="h", caption="c", call_to_action="cta", hashtags=[], channel_variants=[])
    await service.save_campaign_content(version.campaign_id, 1, "campaign_copy", copy, idempotency_key="c2")
    scenes = [
        StoryboardScene(
            scene_number=i, purpose="p", duration_seconds=5, narration="n", visual_prompt="v", transition="cut"
        )
        for i in (1, 2, 3)
    ]
    storyboard = Storyboard(scenes=scenes, total_duration_seconds=15)
    await service.save_campaign_content(version.campaign_id, 1, "storyboard", storyboard, idempotency_key="c3")
    assert await service.validate_campaign_package(version.campaign_id) is True


@pytest.mark.asyncio
async def test_prepare_delivery_package_is_idempotent():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    first = await service.prepare_delivery_package(version.campaign_id, 1, idempotency_key="pkg-1")
    second = await service.prepare_delivery_package(version.campaign_id, 1, idempotency_key="pkg-1")
    assert first == second
