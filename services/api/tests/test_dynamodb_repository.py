from datetime import UTC, datetime
from uuid import uuid4

import boto3
import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus
from fastapi.testclient import TestClient
from moto import mock_aws

from campaign_api.config import Settings
from campaign_api.exceptions import DuplicateCampaign, InvalidStateTransition, RepositoryFailure
from campaign_api.main import create_app
from campaign_api.queue.in_memory_job_queue import InMemoryJobQueue
from campaign_api.repositories.dynamodb_campaign_repository import DynamoDBCampaignRepository

TABLE = "campaign-test"


def create_table(client):
    client.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def records():
    now = datetime.now(UTC)
    cid = uuid4()
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew",
        business_description="A local roaster offering weekly delivery.",
        campaign_goal="sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )
    aggregate = CampaignAggregateMetadata(
        campaign_id=cid,
        current_version=1,
        title="Example",
        created_at=now,
        updated_at=now,
        lock_version=0,
        event_sequence=0,
        current_status=CampaignStatus.CREATED,
        current_progress=0,
    )
    version = CampaignVersion(
        campaign_id=cid,
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.CREATED,
        progress_percent=0,
        brief=brief,
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=0,
        checkpoint_version=0,
    )
    return aggregate, version


@pytest.fixture
def dynamodb():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        yield client


@pytest.fixture
def repository(dynamodb):
    return DynamoDBCampaignRepository(dynamodb, TABLE)


@pytest.mark.asyncio
async def test_atomic_create_get_list_and_duplicate(repository, dynamodb):
    aggregate, version = records()
    await repository.create_initial(aggregate, version)
    assert await repository.exists(aggregate.campaign_id)
    found = await repository.get(aggregate.campaign_id)
    assert found is not None
    assert found[0].model_dump(mode="json", by_alias=True) == aggregate.model_dump(mode="json", by_alias=True)
    assert found[1].model_dump(mode="json", by_alias=True) == version.model_dump(mode="json", by_alias=True)
    listed = await repository.list(offset=0, limit=10)
    assert [x[0].campaign_id for x in listed] == [aggregate.campaign_id]
    with pytest.raises(DuplicateCampaign):
        await repository.create_initial(aggregate, version)
    scan = dynamodb.scan(TableName=TABLE)["Items"]
    assert len(scan) == 2
    assert all(not isinstance(value, float) for item in scan for value in item.values())


@pytest.mark.asyncio
async def test_optimistic_replace_and_conflict(repository):
    aggregate, version = records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    queued_a = aggregate.model_copy(
        update={"current_status": CampaignStatus.QUEUED, "current_progress": 2, "updated_at": now, "lock_version": 1}
    )
    queued_v = version.model_copy(
        update={"status": CampaignStatus.QUEUED, "progress_percent": 2, "updated_at": now, "lock_version": 1}
    )
    await repository.replace_current(queued_a, queued_v)
    found = await repository.get(aggregate.campaign_id)
    assert found is not None and found[1].status == CampaignStatus.QUEUED and found[1].lock_version == 1
    with pytest.raises(InvalidStateTransition):
        await repository.replace_current(queued_a, queued_v)


@pytest.mark.asyncio
async def test_guarded_rollback(repository):
    aggregate, version = records()
    await repository.create_initial(aggregate, version)
    await repository.rollback_initial(aggregate.campaign_id)
    assert not await repository.exists(aggregate.campaign_id)
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    await repository.replace_current(
        aggregate.model_copy(
            update={
                "current_status": CampaignStatus.QUEUED,
                "current_progress": 2,
                "updated_at": now,
                "lock_version": 1,
            }
        ),
        version.model_copy(
            update={"status": CampaignStatus.QUEUED, "progress_percent": 2, "updated_at": now, "lock_version": 1}
        ),
    )
    with pytest.raises(InvalidStateTransition):
        await repository.rollback_initial(aggregate.campaign_id)


@pytest.mark.asyncio
async def test_health_and_cleanup_guard(repository, dynamodb):
    assert await repository.available()
    dynamodb.delete_table(TableName=TABLE)
    assert not await repository.available()
    with pytest.raises(RepositoryFailure):
        await repository.clear()


def test_api_queue_failure_rolls_back_dynamo(repository):
    queue = InMemoryJobQueue()
    queue.fail_submissions = True
    app = create_app(Settings(dynamodb_table_name=TABLE), repository, queue)
    request = {
        "business_name": "Example Coffee",
        "product_or_service": "Cold brew",
        "business_description": "A local roaster offering weekly delivery.",
        "campaign_goal": "sales",
        "platforms": ["instagram"],
        "tone": "bright",
        "language": "en-US",
    }
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/campaigns", json=request, headers={"Idempotency-Key": "rollback-test"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    assert boto3.client("dynamodb", region_name="us-east-1").scan(TableName=TABLE)["Count"] == 0
