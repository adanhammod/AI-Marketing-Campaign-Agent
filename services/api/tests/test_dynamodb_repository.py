from datetime import UTC, datetime
from uuid import uuid4, uuid5

import boto3
import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import (
    ApprovalRecord,
    CampaignAggregateMetadata,
    CampaignConstraints,
    CampaignVersion,
    RetryMetadata,
    ReviewPackage,
)
from campaign_contracts.enums import CampaignStatus, WorkflowStep
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


def ready_for_review_records():
    aggregate, version = records()
    checksum = "a" * 64
    ready_aggregate = aggregate.model_copy(update={"current_status": CampaignStatus.READY_FOR_REVIEW})
    ready_version = version.model_copy(
        update={
            "status": CampaignStatus.READY_FOR_REVIEW,
            "review_package": ReviewPackage(artifact_id=uuid4(), manifest_checksum=checksum, artifact_ids=[uuid4()]),
        }
    )
    return ready_aggregate, ready_version, checksum


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
async def test_approve_persists_new_job_id_and_approval_item(repository, dynamodb):
    aggregate, version, checksum = ready_for_review_records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    resume_job_id = uuid5(version.campaign_id, f"RESUME:{version.campaign_version}")
    approval = ApprovalRecord(
        approval_id=uuid4(),
        campaign_id=version.campaign_id,
        campaign_version=version.campaign_version,
        approved_at=now,
        manifest_checksum=checksum,
        note=None,
        created_at=now,
    )
    approved_version = version.model_copy(
        update={
            "status": CampaignStatus.APPROVED,
            "job_id": resume_job_id,
            "approval": approval,
            "progress_percent": 98,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    approved_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.APPROVED, "current_progress": 98, "updated_at": now, "lock_version": 1}
    )

    await repository.approve(approved_aggregate, approved_version, approval)

    # the raw VERSION item's job_id attribute is exactly what the worker's lease
    # acquisition ConditionExpression compares against -- prove it directly, not just
    # via the round-tripped Pydantic model.
    raw = dynamodb.get_item(
        TableName=TABLE, Key={"PK": {"S": f"CAMPAIGN#{version.campaign_id}"}, "SK": {"S": "VERSION#1"}}
    )["Item"]
    assert raw["job_id"]["S"] == str(resume_job_id)
    assert raw["status"]["S"] == "APPROVED"

    found = await repository.get(aggregate.campaign_id)
    assert found is not None
    assert found[1].job_id == resume_job_id
    assert found[1].approval is not None and found[1].approval.manifest_checksum == checksum

    approval_item = dynamodb.get_item(
        TableName=TABLE, Key={"PK": {"S": f"CAMPAIGN#{version.campaign_id}"}, "SK": {"S": "APPROVAL#1"}}
    ).get("Item")
    assert approval_item is not None and approval_item["decision"]["S"] == "APPROVED"


@pytest.mark.asyncio
async def test_approve_conflicts_on_concurrent_double_approval(repository):
    aggregate, version, checksum = ready_for_review_records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    resume_job_id = uuid5(version.campaign_id, f"RESUME:{version.campaign_version}")
    approval = ApprovalRecord(
        approval_id=uuid4(),
        campaign_id=version.campaign_id,
        campaign_version=version.campaign_version,
        approved_at=now,
        manifest_checksum=checksum,
        note=None,
        created_at=now,
    )
    approved_version = version.model_copy(
        update={
            "status": CampaignStatus.APPROVED,
            "job_id": resume_job_id,
            "approval": approval,
            "progress_percent": 98,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    approved_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.APPROVED, "current_progress": 98, "updated_at": now, "lock_version": 1}
    )
    await repository.approve(approved_aggregate, approved_version, approval)

    with pytest.raises(InvalidStateTransition):
        await repository.approve(approved_aggregate, approved_version, approval)


@pytest.mark.asyncio
async def test_cancel_persists_reason_and_timestamp(repository, dynamodb):
    aggregate, version = records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    cancelled_version = version.model_copy(
        update={
            "status": CampaignStatus.CANCELLED,
            "cancellation_reason": "User stopped generation",
            "cancelled_at": now,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    cancelled_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.CANCELLED, "updated_at": now, "lock_version": 1}
    )

    await repository.cancel(cancelled_aggregate, cancelled_version)

    raw = dynamodb.get_item(
        TableName=TABLE, Key={"PK": {"S": f"CAMPAIGN#{version.campaign_id}"}, "SK": {"S": "VERSION#1"}}
    )["Item"]
    assert raw["status"]["S"] == "CANCELLED"
    assert raw["cancellation_reason"]["S"] == "User stopped generation"
    assert "cancelled_at" in raw

    found = await repository.get(aggregate.campaign_id)
    assert found is not None
    assert found[1].status == CampaignStatus.CANCELLED
    assert found[1].cancellation_reason == "User stopped generation"
    assert found[1].cancelled_at is not None
    # artifacts/content fields are untouched by cancel()
    assert found[1].brief.model_dump(mode="json") == version.brief.model_dump(mode="json")


@pytest.mark.asyncio
async def test_cancel_conflicts_on_concurrent_double_cancel(repository):
    aggregate, version = records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    cancelled_version = version.model_copy(
        update={
            "status": CampaignStatus.CANCELLED,
            "cancellation_reason": "reason",
            "cancelled_at": now,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    cancelled_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.CANCELLED, "updated_at": now, "lock_version": 1}
    )
    await repository.cancel(cancelled_aggregate, cancelled_version)

    with pytest.raises(InvalidStateTransition):
        await repository.cancel(cancelled_aggregate, cancelled_version)


@pytest.mark.asyncio
async def test_cancel_vs_worker_transition_lock_conflict(repository):
    """Proves race #1 from the C2 plan: if a worker (or any other writer) advances
    lock_version on the VERSION item between the API's read and its cancel() write,
    the cancel() write is rejected rather than silently succeeding against stale data."""
    aggregate, version = records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    # Simulate a worker's concurrent transition (e.g. QUEUED -> GENERATING_STRATEGY)
    # advancing lock_version to 1 on both items, same shape as replace_current.
    worker_advanced_version = version.model_copy(
        update={"status": CampaignStatus.GENERATING_STRATEGY, "updated_at": now, "lock_version": 1}
    )
    worker_advanced_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.GENERATING_STRATEGY, "updated_at": now, "lock_version": 1}
    )
    await repository.replace_current(worker_advanced_aggregate, worker_advanced_version)

    # The API had read the campaign before the worker's transition, so it still
    # expects lock_version 0 -> 1, which is now stale (actual is already 1 -> 2).
    stale_cancel_version = version.model_copy(
        update={
            "status": CampaignStatus.CANCELLED,
            "cancellation_reason": "reason",
            "cancelled_at": now,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    stale_cancel_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.CANCELLED, "updated_at": now, "lock_version": 1}
    )
    with pytest.raises(InvalidStateTransition):
        await repository.cancel(stale_cancel_aggregate, stale_cancel_version)

    # The worker's transition is untouched -- no partial/corrupted write occurred.
    found = await repository.get(aggregate.campaign_id)
    assert found is not None and found[1].status == CampaignStatus.GENERATING_STRATEGY


@pytest.mark.asyncio
async def test_cancel_vs_approve_race_lock_conflict(repository):
    """Proves the cancel-vs-approve race from the C2 required-test list: if approve()
    commits first (advancing lock_version and status to APPROVED), a stale cancel()
    attempt that still expects the pre-approval lock_version is rejected rather than
    silently reviving/overwriting the approved campaign."""
    aggregate, version, checksum = ready_for_review_records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    resume_job_id = uuid5(version.campaign_id, f"RESUME:{version.campaign_version}")
    approval = ApprovalRecord(
        approval_id=uuid4(),
        campaign_id=version.campaign_id,
        campaign_version=version.campaign_version,
        approved_at=now,
        manifest_checksum=checksum,
        note=None,
        created_at=now,
    )
    approved_version = version.model_copy(
        update={
            "status": CampaignStatus.APPROVED,
            "job_id": resume_job_id,
            "approval": approval,
            "progress_percent": 98,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    approved_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.APPROVED, "current_progress": 98, "updated_at": now, "lock_version": 1}
    )
    await repository.approve(approved_aggregate, approved_version, approval)

    # The API had read the campaign before the approval committed, so its cancel
    # attempt still expects lock_version 0 -> 1, which is now stale (actual is 1 -> 2).
    stale_cancel_version = version.model_copy(
        update={
            "status": CampaignStatus.CANCELLED,
            "cancellation_reason": "reason",
            "cancelled_at": now,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    stale_cancel_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.CANCELLED, "updated_at": now, "lock_version": 1}
    )
    with pytest.raises(InvalidStateTransition):
        await repository.cancel(stale_cancel_aggregate, stale_cancel_version)

    # The approval is untouched -- cancel's stale write did not corrupt state.
    found = await repository.get(aggregate.campaign_id)
    assert found is not None and found[1].status == CampaignStatus.APPROVED


def _failed_records():
    aggregate, version = records()
    retry_meta = RetryMetadata(attempt=1, max_attempts=3, retryable=True, resume_step=WorkflowStep.IMAGES)
    failed_aggregate = aggregate.model_copy(update={"current_status": CampaignStatus.FAILED})
    failed_version = version.model_copy(update={"status": CampaignStatus.FAILED, "retry": retry_meta})
    return failed_aggregate, failed_version


@pytest.mark.asyncio
async def test_retry_persists_status_and_job_id(repository, dynamodb):
    aggregate, version = _failed_records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    retry_job_id = uuid5(version.campaign_id, f"RETRY:{version.campaign_version}:{version.retry.attempt}")
    queued_version = version.model_copy(
        update={"status": CampaignStatus.QUEUED, "job_id": retry_job_id, "updated_at": now, "lock_version": 1}
    )
    queued_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.QUEUED, "updated_at": now, "lock_version": 1}
    )

    await repository.retry(queued_aggregate, queued_version)

    raw = dynamodb.get_item(
        TableName=TABLE, Key={"PK": {"S": f"CAMPAIGN#{version.campaign_id}"}, "SK": {"S": "VERSION#1"}}
    )["Item"]
    assert raw["status"]["S"] == "QUEUED"
    assert raw["job_id"]["S"] == str(retry_job_id)

    found = await repository.get(aggregate.campaign_id)
    assert found is not None
    assert found[1].status == CampaignStatus.QUEUED
    assert found[1].job_id == retry_job_id
    # RetryMetadata is worker-owned; retry() must not touch it.
    assert found[1].retry == version.retry


@pytest.mark.asyncio
async def test_retry_conflicts_on_concurrent_double_retry(repository):
    aggregate, version = _failed_records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    retry_job_id = uuid5(version.campaign_id, f"RETRY:{version.campaign_version}:{version.retry.attempt}")
    queued_version = version.model_copy(
        update={"status": CampaignStatus.QUEUED, "job_id": retry_job_id, "updated_at": now, "lock_version": 1}
    )
    queued_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.QUEUED, "updated_at": now, "lock_version": 1}
    )
    await repository.retry(queued_aggregate, queued_version)

    with pytest.raises(InvalidStateTransition):
        await repository.retry(queued_aggregate, queued_version)


def _revision_records():
    aggregate, version, _ = ready_for_review_records()
    now = datetime.now(UTC)
    regenerate_job_id = uuid5(version.campaign_id, "REGENERATE:2")
    frozen_parent = version.model_copy(
        update={"status": CampaignStatus.REVISION_REQUESTED, "updated_at": now, "lock_version": 1}
    )
    child_version = version.model_copy(
        update={
            "campaign_version": 2,
            "parent_version": 1,
            "job_id": regenerate_job_id,
            "status": CampaignStatus.QUEUED,
            "current_step": WorkflowStep.COPY,
            "progress_percent": 20,
            "review_package": None,
            "created_at": now,
            "updated_at": now,
            "lock_version": 0,
        }
    )
    updated_aggregate = aggregate.model_copy(
        update={"current_version": 2, "current_status": CampaignStatus.QUEUED, "updated_at": now, "lock_version": 1}
    )
    return aggregate, version, updated_aggregate, frozen_parent, child_version


@pytest.mark.asyncio
async def test_revise_persists_parent_freeze_child_creation_and_meta_atomically(repository, dynamodb):
    aggregate, version, updated_aggregate, frozen_parent, child_version = _revision_records()
    await repository.create_initial(aggregate, version)

    await repository.revise(updated_aggregate, frozen_parent, child_version)

    parent_raw = dynamodb.get_item(
        TableName=TABLE, Key={"PK": {"S": f"CAMPAIGN#{version.campaign_id}"}, "SK": {"S": "VERSION#1"}}
    )["Item"]
    assert parent_raw["status"]["S"] == "REVISION_REQUESTED"

    child_raw = dynamodb.get_item(
        TableName=TABLE, Key={"PK": {"S": f"CAMPAIGN#{version.campaign_id}"}, "SK": {"S": "VERSION#2"}}
    )["Item"]
    assert child_raw["status"]["S"] == "QUEUED"
    assert child_raw["parent_version"]["N"] == "1"

    meta_raw = dynamodb.get_item(
        TableName=TABLE, Key={"PK": {"S": f"CAMPAIGN#{version.campaign_id}"}, "SK": {"S": "META"}}
    )["Item"]
    assert meta_raw["current_version"]["N"] == "2"
    assert meta_raw["current_status"]["S"] == "QUEUED"

    found = await repository.get(aggregate.campaign_id)
    assert found is not None
    assert found[1].campaign_version == 2
    assert found[1].status == CampaignStatus.QUEUED
    assert found[1].parent_version == 1


@pytest.mark.asyncio
async def test_revise_conflicts_on_concurrent_double_revision(repository):
    aggregate, version, updated_aggregate, frozen_parent, child_version = _revision_records()
    await repository.create_initial(aggregate, version)
    await repository.revise(updated_aggregate, frozen_parent, child_version)

    with pytest.raises(InvalidStateTransition):
        await repository.revise(updated_aggregate, frozen_parent, child_version)


@pytest.mark.asyncio
async def test_revise_vs_approve_race_lock_conflict(repository):
    """Proves the revision-vs-approve race: if approve() commits first (advancing
    lock_version and status to APPROVED), a stale revise() attempt still expecting the
    pre-approval lock_version is rejected rather than silently corrupting state."""
    aggregate, version, checksum = ready_for_review_records()
    await repository.create_initial(aggregate, version)
    now = datetime.now(UTC)
    resume_job_id = uuid5(version.campaign_id, "RESUME:1")
    approval = ApprovalRecord(
        approval_id=uuid4(),
        campaign_id=version.campaign_id,
        campaign_version=1,
        approved_at=now,
        manifest_checksum=checksum,
        note=None,
        created_at=now,
    )
    approved_version = version.model_copy(
        update={
            "status": CampaignStatus.APPROVED,
            "job_id": resume_job_id,
            "approval": approval,
            "progress_percent": 98,
            "updated_at": now,
            "lock_version": 1,
        }
    )
    approved_aggregate = aggregate.model_copy(
        update={"current_status": CampaignStatus.APPROVED, "current_progress": 98, "updated_at": now, "lock_version": 1}
    )
    await repository.approve(approved_aggregate, approved_version, approval)

    # The API had read the campaign before the approval committed, so its revise
    # attempt still expects lock_version 0 -> 1, which is now stale (actual is 1 -> 2).
    regenerate_job_id = uuid5(version.campaign_id, "REGENERATE:2")
    stale_frozen_parent = version.model_copy(
        update={"status": CampaignStatus.REVISION_REQUESTED, "updated_at": now, "lock_version": 1}
    )
    stale_child = version.model_copy(
        update={
            "campaign_version": 2,
            "parent_version": 1,
            "job_id": regenerate_job_id,
            "status": CampaignStatus.QUEUED,
            "review_package": None,
            "updated_at": now,
            "lock_version": 0,
        }
    )
    stale_aggregate = aggregate.model_copy(
        update={"current_version": 2, "current_status": CampaignStatus.QUEUED, "updated_at": now, "lock_version": 1}
    )

    with pytest.raises(InvalidStateTransition):
        await repository.revise(stale_aggregate, stale_frozen_parent, stale_child)

    # The approval is untouched -- revise's stale write did not corrupt state.
    found = await repository.get(aggregate.campaign_id)
    assert found is not None and found[1].status == CampaignStatus.APPROVED


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
