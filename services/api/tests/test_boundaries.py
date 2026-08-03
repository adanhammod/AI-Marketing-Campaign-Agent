from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus, SQSOperation
from campaign_contracts.sqs import SQSJobMessage

from campaign_api.exceptions import DuplicateCampaign, DuplicateJobConflict
from campaign_api.queue.in_memory_job_queue import InMemoryJobQueue
from campaign_api.repositories.in_memory_campaign_repository import InMemoryCampaignRepository


def request():
    return CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew",
        business_description="A local roaster offering weekly delivery.",
        campaign_goal="sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )


def records():
    now = datetime.now(UTC)
    cid = uuid4()
    aggregate = CampaignAggregateMetadata(
        campaign_id=cid, current_version=1, title="x", created_at=now, updated_at=now, lock_version=0
    )
    version = CampaignVersion(
        campaign_id=cid,
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.CREATED,
        progress_percent=0,
        brief=request(),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
    )
    return aggregate, version


@pytest.mark.asyncio
async def test_repository_duplicate_safe_copy_and_cleanup():
    repo = InMemoryCampaignRepository()
    a, v = records()
    await repo.create_initial(a, v)
    with pytest.raises(DuplicateCampaign):
        await repo.create_initial(a, v)
    fetched = await repo.get(a.campaign_id)
    assert fetched is not None
    fetched[0].title = "mutated"
    assert (await repo.get(a.campaign_id))[0].title == "x"
    assert await repo.exists(a.campaign_id)
    await repo.clear()
    assert not await repo.exists(a.campaign_id)


@pytest.mark.asyncio
async def test_queue_duplicate_safe_and_conflict():
    queue = InMemoryJobQueue()
    now = datetime.now(UTC)
    job = uuid4()
    base = dict(
        schema_version=1,
        job_id=job,
        campaign_id=uuid4(),
        campaign_version=1,
        operation=SQSOperation.START,
        requested_step=None,
        revision_scope=None,
        idempotency_key="x",
        correlation_id=uuid4(),
        requested_at=now,
        attempt=0,
        trace_id=None,
    )
    message = SQSJobMessage(**base)
    assert (await queue.submit(message)).accepted
    assert (await queue.submit(message)).accepted
    assert len(queue.messages()) == 1
    conflict = message.model_copy(update={"campaign_id": uuid4()})
    with pytest.raises(DuplicateJobConflict):
        await queue.submit(conflict)
    await queue.clear()
    assert queue.messages() == []
