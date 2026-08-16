import asyncio
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3
import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.artifacts import ArtifactAttribution, ImageArtifactReference
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.dynamodb import meta_sk, pk, serialize_meta, serialize_version, version_sk
from campaign_contracts.enums import (
    Actor,
    CampaignEventType,
    CampaignStatus,
    ErrorComponent,
    SQSOperation,
    StepStatus,
    WorkflowStep,
)
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.events import CampaignEvent
from campaign_contracts.sqs import SQSJobMessage
from campaign_contracts.steps import WorkflowStepRecord
from moto import mock_aws
from pydantic import HttpUrl

from campaign_worker.errors import LeaseConflict, LeaseLost, PersistenceUnavailable
from campaign_worker.repositories.dynamodb_workflow_repository import DynamoDBWorkflowRepository

TABLE = "campaign-worker-test"
SERIALIZER = TypeSerializer()

DESERIALIZER = TypeDeserializer()


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


def _pexels_attribution() -> ArtifactAttribution:
    return ArtifactAttribution(
        provider_asset_id="123",
        creator_name="Creator",
        creator_profile_url="https://www.pexels.com/@creator",
        source_page_url="https://www.pexels.com/photo/example-123/",
        provider_url="https://www.pexels.com",
        attribution_text="Photo by Creator on Pexels",
    )


def _image_artifact(loaded: CampaignVersion, now: datetime) -> ImageArtifactReference:
    return ImageArtifactReference(
        artifact_id=uuid4(),
        campaign_id=loaded.campaign_id,
        campaign_version=loaded.campaign_version,
        workflow_step=WorkflowStep.IMAGES,
        mime_type="image/jpeg",
        size_bytes=42,
        checksum_sha256="a" * 64,
        created_at=now,
        provider="pexels",
        scene_number=1,
        attribution=_pexels_attribution(),
    )


@pytest.mark.asyncio
async def test_save_version_persists_image_artifacts_with_http_url_attribution(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    artifact = _image_artifact(loaded, now)
    updated = loaded.model_copy(update={"image_artifacts": [artifact]})

    await repository.save_version(updated, lease)

    raw = client.get_item(TableName=TABLE, Key=marshal({"PK": pk(message.campaign_id), "SK": version_sk(1)}))["Item"]
    stored_url = raw["image_artifacts"]["L"][0]["M"]["attribution"]["M"]["creator_profile_url"]
    assert stored_url == {"S": "https://www.pexels.com/@creator"}

    reloaded = await repository.load_version(message)
    assert reloaded.image_artifacts[0].attribution.creator_profile_url == HttpUrl("https://www.pexels.com/@creator")


@pytest.mark.asyncio
async def test_save_version_persists_failed_version_with_image_artifacts(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    artifact = _image_artifact(loaded, now)
    error = SanitizedWorkflowError(
        code="STORAGE_UNAVAILABLE",
        message="artifact reconciliation unavailable",
        component=ErrorComponent.LANGGRAPH_WORKER,
        workflow_step=WorkflowStep.IMAGES,
        attempt=1,
        retryable=True,
        timestamp=now,
        correlation_id=uuid4(),
        campaign_id=loaded.campaign_id,
        campaign_version=loaded.campaign_version,
        job_id=loaded.job_id,
    )
    failed = loaded.model_copy(update={"image_artifacts": [artifact], "status": CampaignStatus.FAILED, "error": error})

    # Must not raise -- mirrors the failure-persistence path (job_processor._fail ->
    # handle_failure -> save_version), which crashed on this same HttpUrl-bearing
    # version before the _ddb() fix.
    await repository.save_version(failed, lease)

    reloaded = await repository.load_version(message)
    assert reloaded.status == CampaignStatus.FAILED
    assert reloaded.error is not None
    assert reloaded.error.code == "STORAGE_UNAVAILABLE"


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


@pytest.mark.asyncio
async def test_already_running_worker_cannot_overwrite_a_concurrent_api_cancellation(database):
    """D: an already-running worker (holding a lease acquired before an external
    cancellation) loses its stale lease and cannot overwrite CANCELLED. Simulates the
    campaign_api DynamoDBCampaignRepository.cancel() write directly against the same
    VERSION item -- worker and API share the same lock_version counter on that item."""
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    in_progress = loaded.model_copy(update={"status": CampaignStatus.GENERATING_STRATEGY})

    # Simulate the API's cancel() committing while the worker is mid-node: it bumps
    # lock_version (ADD, exactly like campaign_api's cancel()/replace_current()) and
    # sets status=CANCELLED, independent of the worker's lease.
    client.update_item(
        TableName=TABLE,
        Key=marshal({"PK": pk(message.campaign_id), "SK": version_sk(message.campaign_version)}),
        UpdateExpression="SET #status=:status, cancellation_reason=:reason ADD lock_version :one",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": SERIALIZER.serialize("CANCELLED"),
            ":reason": SERIALIZER.serialize("User stopped generation"),
            ":one": SERIALIZER.serialize(1),
        },
    )

    # The worker's in-flight node finishes and tries to persist its (now stale) result.
    with pytest.raises(LeaseLost):
        await repository.save_version(in_progress, lease)

    # The API's cancellation is untouched -- the worker's overwrite attempt failed.
    reloaded = await repository.load_version(message)
    assert reloaded is not None
    assert reloaded.status == CampaignStatus.CANCELLED


def event_for(message: SQSJobMessage, event_type: CampaignEventType, discriminator: str) -> CampaignEvent:
    from campaign_worker.events import deterministic_event_id

    return CampaignEvent(
        event_id=deterministic_event_id(
            message.campaign_id,
            message.campaign_version,
            event_type,
            discriminator,
        ),
        campaign_id=message.campaign_id,
        campaign_version=message.campaign_version,
        event_sequence=1,
        event_type=event_type,
        status=CampaignStatus.GENERATING_STRATEGY,
        step=WorkflowStep.STRATEGY,
        progress_percent=10,
        occurred_at=datetime.now(UTC),
        actor=Actor.LANGGRAPH_WORKER,
        correlation_id=message.correlation_id,
        job_id=message.job_id,
    )


def event_items(client, campaign_id):
    response = client.query(
        TableName=TABLE,
        KeyConditionExpression="PK=:pk AND begins_with(SK,:prefix)",
        ExpressionAttributeValues={
            ":pk": SERIALIZER.serialize(pk(campaign_id)),
            ":prefix": SERIALIZER.serialize("EVENT#"),
        },
    )
    return [{key: DESERIALIZER.deserialize(value) for key, value in item.items()} for item in response["Items"]]


def meta_sequence(client, campaign_id):
    response = client.get_item(
        TableName=TABLE,
        Key=marshal({"PK": pk(campaign_id), "SK": meta_sk()}),
        ConsistentRead=True,
    )
    return int(DESERIALIZER.deserialize(response["Item"]["event_sequence"]))


@pytest.mark.asyncio
async def test_event_carrying_step_write_is_atomic_and_advances_sequence(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    record = WorkflowStepRecord(
        campaign_id=message.campaign_id,
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )

    await repository.save_step(
        record,
        [event_for(message, CampaignEventType.STEP_STARTED, "STRATEGY:0")],
    )

    assert meta_sequence(client, message.campaign_id) == 1
    items = event_items(client, message.campaign_id)
    assert len(items) == 1
    assert items[0]["event_sequence"] == 1
    assert items[0]["event_type"] == CampaignEventType.STEP_STARTED.value
    saved = await repository.get_step(message.campaign_id, 1, WorkflowStep.STRATEGY)
    assert saved is not None and saved.status == StepStatus.RUNNING


@pytest.mark.asyncio
async def test_duplicate_deterministic_event_is_a_noop_without_sequence_increment(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    record = WorkflowStepRecord(
        campaign_id=message.campaign_id,
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.REUSED,
        created_at=now,
        updated_at=now,
    )
    event = event_for(message, CampaignEventType.STEP_REUSED, WorkflowStep.STRATEGY.value)

    await repository.save_step(record, [event])
    await repository.save_step(record, [event])

    assert meta_sequence(client, message.campaign_id) == 1
    assert len(event_items(client, message.campaign_id)) == 1


@pytest.mark.asyncio
async def test_event_sequence_collision_is_retried_and_remains_gapless(database, monkeypatch):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    original = client.transact_write_items
    attempts = 0

    def collide_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException", "Message": "collision"},
                    "CancellationReasons": [
                        {"Code": "None"},
                        {"Code": "ConditionalCheckFailed"},
                        {"Code": "None"},
                    ],
                },
                "TransactWriteItems",
            )
        return original(**kwargs)

    monkeypatch.setattr(client, "transact_write_items", collide_once)
    now = datetime.now(UTC)
    record = WorkflowStepRecord(
        campaign_id=message.campaign_id,
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )

    await repository.save_step(record, [event_for(message, CampaignEventType.STEP_STARTED, "STRATEGY:0")])

    assert attempts == 2
    assert meta_sequence(client, message.campaign_id) == 1
    assert [item["event_sequence"] for item in event_items(client, message.campaign_id)] == [1]


@pytest.mark.asyncio
async def test_event_sequence_collision_exhausts_bounded_retry_budget(database, monkeypatch):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)

    def always_collide(**kwargs):
        raise ClientError(
            {
                "Error": {"Code": "TransactionCanceledException", "Message": "collision"},
                "CancellationReasons": [
                    {"Code": "None"},
                    {"Code": "ConditionalCheckFailed"},
                    {"Code": "None"},
                ],
            },
            "TransactWriteItems",
        )

    monkeypatch.setattr(client, "transact_write_items", always_collide)
    now = datetime.now(UTC)
    record = WorkflowStepRecord(
        campaign_id=message.campaign_id,
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )

    with pytest.raises(PersistenceUnavailable, match="retry budget"):
        await repository.save_step(record, [event_for(message, CampaignEventType.STEP_STARTED, "STRATEGY:0")])


@pytest.mark.asyncio
async def test_duplicate_event_does_not_swallow_a_real_lease_conflict(database):
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    assert loaded is not None
    event = event_for(message, CampaignEventType.FAILED, "1")

    await repository.save_version(loaded, lease, [event])

    wrong_owner = lease.__class__("worker-b", lease.lock_version, lease.expires_at)
    with pytest.raises(LeaseLost):
        await repository.save_version(loaded, wrong_owner, [event])


def _gate_update_item(client, *, skip_if):
    """Wraps client.update_item so a call NOT matching skip_if (i.e. the call under
    test) announces it has been dispatched and then blocks until released -- lets a
    test force a concurrent second call to run and commit first, deterministically,
    with no reliance on sleep-based timing."""
    dispatched = threading.Event()
    proceed = threading.Event()
    original = client.update_item

    def gated(**kwargs):
        if skip_if(kwargs):
            return original(**kwargs)
        dispatched.set()
        proceed.wait(timeout=5)
        return original(**kwargs)

    client.update_item = gated
    return dispatched, proceed


def _is_heartbeat_call(kwargs):
    return "ADD lock_version" in kwargs.get("UpdateExpression", "")


@pytest.mark.asyncio
async def test_heartbeat_and_save_version_do_not_self_race(database):
    """A: reproduces the production LeaseLost("campaign version save conflict") --
    save_version captures a lock_version, is still in flight when a concurrent
    heartbeat commits its own ADD lock_version first, and (pre-fix) save_version's
    now-stale request then loses the conditional check even though the same worker
    still owns the lease throughout. With the lease.lock fix, heartbeat cannot even
    dispatch its request until save_version has released the lock, so no interleaving
    -- and no stale write -- ever occurs."""
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    updated = loaded.model_copy(update={"status": CampaignStatus.READY_FOR_REVIEW})

    dispatched, proceed = _gate_update_item(client, skip_if=_is_heartbeat_call)

    save_task = asyncio.create_task(repository.save_version(updated, lease))
    await asyncio.to_thread(dispatched.wait, 5)
    assert dispatched.is_set(), "save_version never dispatched its update_item call"

    heartbeat_task = asyncio.create_task(
        repository.heartbeat(message, lease, now + timedelta(seconds=1), now + timedelta(minutes=2))
    )
    await asyncio.sleep(0.05)
    assert not heartbeat_task.done(), "heartbeat should be waiting on lease.lock while save_version is in flight"

    proceed.set()
    await save_task  # must not raise LeaseLost
    refreshed = await heartbeat_task  # must not raise, runs only after save_version releases the lock

    reloaded = await repository.load_version(message)
    assert reloaded.status == CampaignStatus.READY_FOR_REVIEW
    raw_item = client.get_item(TableName=TABLE, Key=marshal({"PK": pk(message.campaign_id), "SK": version_sk(1)}))[
        "Item"
    ]
    assert int(DESERIALIZER.deserialize(raw_item["lock_version"])) == refreshed.lock_version


@pytest.mark.asyncio
async def test_in_process_lock_does_not_mask_a_real_takeover(database):
    """B: post-fix regression guard -- the in-process lease.lock only ever serializes
    this worker's own heartbeat/save/complete/release calls against each other. A
    genuinely different actor (another worker, or the API's cancel()/replace_current(),
    per test_already_running_worker_cannot_overwrite_a_concurrent_api_cancellation)
    still commits independently of this worker's lease.lock, so a real stale
    lock_version must still raise LeaseLost."""
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)

    # Simulate a different actor bumping lock_version directly (not through this
    # worker's lease.lock at all -- a separate DynamoDBWorkflowRepository instance
    # with its own LeaseContext, exactly as a second real worker process would).
    other_repository = DynamoDBWorkflowRepository(client, TABLE)
    other_lease = await other_repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    await other_repository.heartbeat(message, other_lease, now + timedelta(seconds=1), now + timedelta(minutes=2))

    with pytest.raises(LeaseLost):
        await repository.save_version(loaded, lease)


@pytest.mark.asyncio
async def test_repeated_heartbeats_during_long_step_then_save_succeeds(database):
    """C: several heartbeat cycles fire in sequence during a simulated long-running
    graph step; the subsequent save_version must still succeed with a consistent
    lock_version."""
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    updated = loaded.model_copy(update={"status": CampaignStatus.READY_FOR_REVIEW})

    for i in range(5):
        lease = await repository.heartbeat(
            message, lease, now + timedelta(seconds=i + 1), now + timedelta(minutes=2)
        )

    await repository.save_version(updated, lease)

    reloaded = await repository.load_version(message)
    assert reloaded.status == CampaignStatus.READY_FOR_REVIEW
    raw_item = client.get_item(TableName=TABLE, Key=marshal({"PK": pk(message.campaign_id), "SK": version_sk(1)}))[
        "Item"
    ]
    assert int(DESERIALIZER.deserialize(raw_item["lock_version"])) == lease.lock_version


def _gate_transact_write_items(client, *, only_when):
    dispatched = threading.Event()
    proceed = threading.Event()
    original = client.transact_write_items

    def gated(**kwargs):
        if only_when(kwargs.get("TransactItems", [])):
            dispatched.set()
            proceed.wait(timeout=5)
        return original(**kwargs)

    client.transact_write_items = gated
    return dispatched, proceed


def _is_save_version_transaction(items):
    return any(
        "Update" in item
        and "lock_version=:lock" in item["Update"].get("ConditionExpression", "")
        and "ADD lock_version" not in item["Update"].get("UpdateExpression", "")
        for item in items
    )


@pytest.mark.asyncio
async def test_failure_path_save_with_events_does_not_self_race_with_heartbeat(database):
    """D: the FAILED-transition save_version call (job_processor._fail -> save_version
    with events, the transact_write_items branch) must not self-race with a concurrent
    heartbeat either -- mirrors Test A but for the events-bearing code path."""
    client, message = database
    repository = DynamoDBWorkflowRepository(client, TABLE)
    now = datetime.now(UTC)
    lease = await repository.acquire_lease(message, "worker-a", now, now + timedelta(minutes=2))
    loaded = await repository.load_version(message)
    failed = loaded.model_copy(update={"status": CampaignStatus.FAILED})
    event = event_for(message, CampaignEventType.FAILED, "1")

    dispatched, proceed = _gate_transact_write_items(client, only_when=_is_save_version_transaction)

    save_task = asyncio.create_task(repository.save_version(failed, lease, [event]))
    await asyncio.to_thread(dispatched.wait, 5)
    assert dispatched.is_set(), "save_version(events=...) never dispatched its transaction"

    heartbeat_task = asyncio.create_task(
        repository.heartbeat(message, lease, now + timedelta(seconds=1), now + timedelta(minutes=2))
    )
    await asyncio.sleep(0.05)
    assert not heartbeat_task.done(), "heartbeat should be waiting on lease.lock while the failure save is in flight"

    proceed.set()
    await save_task  # must not raise LeaseLost
    await heartbeat_task  # must not raise

    reloaded = await repository.load_version(message)
    assert reloaded.status == CampaignStatus.FAILED
