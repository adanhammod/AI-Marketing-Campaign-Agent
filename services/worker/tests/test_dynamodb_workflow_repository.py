from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3
import pytest
from boto3.dynamodb.types import TypeSerializer
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.dynamodb import serialize_meta, serialize_version
from campaign_contracts.enums import CampaignStatus, SQSOperation, StepStatus, WorkflowStep
from campaign_contracts.sqs import SQSJobMessage
from campaign_contracts.steps import WorkflowStepRecord
from moto import mock_aws

from campaign_worker.errors import LeaseConflict, LeaseLost
from campaign_worker.repositories.dynamodb_workflow_repository import DynamoDBWorkflowRepository

TABLE = "campaign-worker-test"
SERIALIZER = TypeSerializer()


def marshal(item):
    return {key: SERIALIZER.serialize(value) for key, value in item.items()}


def values():
    now = datetime.now(UTC)
    campaign_id = uuid4()
    job_id = uuid4()
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew",
        business_description="A local roaster offering weekly delivery.",
        campaign_goal="increase sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )
    aggregate = CampaignAggregateMetadata(
        campaign_id=campaign_id,
        current_version=1,
        title="Example",
        created_at=now,
        updated_at=now,
        lock_version=1,
        current_status=CampaignStatus.QUEUED,
        current_progress=2,
    )
    version = CampaignVersion(
        campaign_id=campaign_id,
        campaign_version=1,
        job_id=job_id,
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    message = SQSJobMessage(
        schema_version=1,
        job_id=job_id,
        campaign_id=campaign_id,
        campaign_version=1,
        operation=SQSOperation.START,
        idempotency_key="task9-integration",
        correlation_id=uuid4(),
        requested_at=now,
        attempt=0,
    )
    return aggregate, version, message


@pytest.fixture
def database():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        aggregate, version, message = values()
        client.put_item(TableName=TABLE, Item=marshal(serialize_meta(aggregate)))
        client.put_item(TableName=TABLE, Item=marshal(serialize_version(version)))
        yield client, message


@pytest.mark.asyncio
async def test_load_acquire_conflict_heartbeat_release_and_expiry_recovery(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    loaded = await repository.load_version(message)
    assert loaded is not None and loaded.job_id == message.job_id
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    with pytest.raises(LeaseConflict):
        await repository.acquire_lease(message, "worker-b", now, now + timedelta(minutes=2))
    lease = await repository.heartbeat(message, lease, now + timedelta(seconds=2), now + timedelta(minutes=2))
    stale = lease.__class__(lease.owner, lease.lock_version - 1, lease.expires_at)
    with pytest.raises(LeaseLost):
        await repository.heartbeat(message, stale, now + timedelta(seconds=3), now + timedelta(minutes=2))
    await repository.release(message, lease)
    recovered = await repository.acquire_lease(
        message, "worker-b", now + timedelta(minutes=3), now + timedelta(minutes=5)
    )
    assert recovered.owner == "worker-b"


@pytest.mark.asyncio
async def test_completion_checkpoint_duplicate_and_exhaustion_marker(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    assert not await repository.is_completed(message)
    await repository.complete(message, lease, now)
    assert await repository.is_completed(message)
    await repository.record_exhausted(message, 6, now)
    await repository.record_exhausted(message, 7, now)
    assert await repository.available()


@pytest.mark.asyncio
async def test_record_invalid_marker_is_idempotent(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    await repository.record_invalid(message.campaign_id, "UNSUPPORTED_MESSAGE_SCHEMA", "transport-id", now)
    await repository.record_invalid(message.campaign_id, "UNSUPPORTED_MESSAGE_SCHEMA", "transport-id", now)


@pytest.mark.asyncio
async def test_missing_version_and_wrong_job_conflict(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    missing = message.model_copy(update={"campaign_version": 2})
    assert await repository.load_version(missing) is None
    wrong = message.model_copy(update={"job_id": uuid4()})
    with pytest.raises(LeaseConflict):
        await repository.acquire_lease(wrong, "worker-a", datetime.now(UTC), datetime.now(UTC) + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_save_and_get_step_round_trip(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    record = WorkflowStepRecord(
        campaign_id=message.campaign_id,
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.SUCCEEDED,
        attempt=1,
        created_at=now,
        updated_at=now,
    )
    await repository.save_step(record)
    fetched = await repository.get_step(message.campaign_id, 1, WorkflowStep.STRATEGY)
    assert fetched is not None
    assert fetched.status == StepStatus.SUCCEEDED
    assert fetched.attempt == 1


@pytest.mark.asyncio
async def test_get_step_returns_none_when_absent(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    assert await repository.get_step(message.campaign_id, 1, WorkflowStep.COPY) is None


@pytest.mark.asyncio
async def test_save_version_persists_content(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    updated = loaded.model_copy(update={"status": CampaignStatus.READY_FOR_REVIEW})

    await repository.save_version(updated, lease)

    reloaded = await repository.load_version(message)
    assert reloaded.status == CampaignStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_save_version_does_not_clobber_lease_or_lock_version(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)

    await repository.save_version(loaded, lease)

    # If save_version had clobbered lease_owner/lock_version, this heartbeat (which
    # conditions on both) would raise LeaseLost.
    refreshed = await repository.heartbeat(message, lease, now + timedelta(seconds=1), now + timedelta(minutes=2))
    assert refreshed.owner == "worker-a"


@pytest.mark.asyncio
async def test_save_version_raises_lease_lost_for_wrong_owner(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    wrong_owner_lease = lease.__class__("worker-b", lease.lock_version, lease.expires_at)

    with pytest.raises(LeaseLost):
        await repository.save_version(loaded, wrong_owner_lease)


@pytest.mark.asyncio
async def test_save_version_raises_lease_lost_for_stale_lock_version(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    stale_lease = lease.__class__(lease.owner, lease.lock_version - 1, lease.expires_at)

    with pytest.raises(LeaseLost):
        await repository.save_version(loaded, stale_lease)
