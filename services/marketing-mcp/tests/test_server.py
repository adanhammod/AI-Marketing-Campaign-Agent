from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus


@pytest.mark.asyncio
async def test_server_module_imports_and_registers_get_campaign_tool():
    from campaign_marketing_mcp.server import mcp

    tools = await mcp.list_tools()
    tool_names = {tool.name for tool in tools}
    assert "get_campaign" in tool_names


@pytest.mark.asyncio
async def test_get_campaign_tool_returns_none_for_unknown_campaign():
    from campaign_marketing_mcp.server import get_campaign

    assert await get_campaign(str(uuid4())) is None


@pytest.mark.asyncio
async def test_get_campaign_tool_returns_serialized_record_for_known_campaign():
    from campaign_marketing_mcp import server

    now = datetime.now(UTC)
    campaign_id = uuid4()
    aggregate = CampaignAggregateMetadata(
        campaign_id=campaign_id, current_version=1, title="Example", created_at=now, updated_at=now, lock_version=0
    )
    version = CampaignVersion(
        campaign_id=campaign_id,
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.CREATED,
        progress_percent=0,
        brief=CampaignCreationRequest(
            business_name="Example Coffee",
            product_or_service="Cold brew",
            business_description="A local roaster offering weekly delivery.",
            campaign_goal="increase sales",
            platforms=["instagram"],
            tone="bright",
            language="en-US",
        ),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=0,
    )
    await server._service.create_campaign(aggregate, version, idempotency_key="server-test-1")

    result = await server.get_campaign(str(campaign_id))

    assert result is not None
    assert result["aggregate"]["campaign_id"] == str(campaign_id)
    assert result["version"]["campaign_id"] == str(campaign_id)
