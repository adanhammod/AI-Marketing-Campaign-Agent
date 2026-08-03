import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus, SQSOperation
from campaign_contracts.sqs import SQSJobMessage

from campaign_worker.config import Settings
from campaign_worker.consumer.sqs_consumer import MessageOutcome, SQSConsumer
from campaign_worker.errors import (
    ConfigurationError,
    LeaseConflict,
    LeaseLost,
    PersistenceUnavailable,
    ProcessingUncertain,
)
from campaign_worker.repositories.workflow_repository import LeaseContext, WorkflowRepository
from campaign_worker.services.job_processor import JobProcessor, NoOpJobProcessor, ProcessingResult


def job(job_id=None, idempotency="task9"):
    return SQSJobMessage(
        schema_version=1,
        job_id=job_id or uuid4(),
        campaign_id=uuid4(),
        campaign_version=1,
        operation=SQSOperation.START,
        requested_step=None,
        revision_scope=None,
        idempotency_key=idempotency,
        correlation_id=uuid4(),
        requested_at=datetime.now(UTC),
        attempt=0,
        trace_id=None,
    )


def version(value):
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew",
        business_description="A local roaster offering weekly delivery.",
        campaign_goal="increase sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )
    return CampaignVersion(
        campaign_id=value.campaign_id,
        campaign_version=1,
        job_id=value.job_id,
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=value.requested_at,
        updated_at=value.requested_at,
        lock_version=1,
    )


def raw(value, count=1, body=None):
    return {
        "MessageId": "transport-id",
        "ReceiptHandle": "opaque-receipt",
        "Body": body if body is not None else value.model_dump_json(),
        "Attributes": {"ApproximateReceiveCount": str(count)},
    }


class FakeRepository(WorkflowRepository):
    def __init__(self, value):
        self.version = version(value)
        self.lease = None
        self.completed = False
        self.exhausted = []
        self.conflict = False
        self.heartbeat_error = None
        self.available_value = True
        self.invalid = []

    async def load_version(self, message):
        return self.version

    async def acquire_lease(self, message, owner, now, expires_at):
        if self.conflict or message.job_id != self.version.job_id:
            raise LeaseConflict("conflict")
        if self.lease and self.lease.expires_at >= now and self.lease.owner != owner:
            raise LeaseConflict("conflict")
        self.lease = LeaseContext(owner, 2, expires_at)
        return self.lease

    async def heartbeat(self, message, lease, now, expires_at):
        if self.heartbeat_error:
            self.lease = None
            raise self.heartbeat_error("heartbeat")
        if self.lease is None or lease.owner != self.lease.owner:
            raise LeaseLost("lost")
        self.lease = LeaseContext(lease.owner, lease.lock_version + 1, expires_at)
        return self.lease

    async def is_completed(self, message):
        return self.completed

    async def complete(self, message, lease, completed_at):
        if self.lease is None or lease.owner != self.lease.owner:
            raise LeaseLost("lost")
        self.completed = True
        self.lease = None

    async def release(self, message, lease):
        if self.lease is None:
            raise LeaseLost("lost")
        self.lease = None

    async def record_exhausted(self, message, receive_count, now):
        self.exhausted.append(receive_count)

    async def record_invalid(self, campaign_id, code, message_id, now):
        self.invalid.append((campaign_id, code))

    async def available(self):
        return self.available_value

    async def get_step(self, campaign_id, campaign_version, step):
        return None

    async def save_step(self, record):
        pass


class StubSQS:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.receives = 0
        self.deletes = []
        self.visibility = 0
        self.visibility_failures = 0
        self.health = True

    def receive_message(self, **kwargs):
        self.receives += 1
        return {"Messages": self.messages}

    def delete_message(self, **kwargs):
        self.deletes.append(kwargs["ReceiptHandle"])
        return {}

    def change_message_visibility(self, **kwargs):
        self.visibility += 1
        if self.visibility_failures > 0:
            self.visibility_failures -= 1
            raise ClientError({"Error": {"Code": "Throttled", "Message": "private"}}, "ChangeMessageVisibility")
        return {}

    def get_queue_attributes(self, **kwargs):
        if not self.health:
            raise ClientError({"Error": {"Code": "Denied", "Message": "private"}}, "GetQueueAttributes")
        return {"Attributes": {"ApproximateNumberOfMessages": "0"}}


class SlowProcessor(JobProcessor):
    def __init__(self, delay=0.04):
        self.delay = delay
        self.calls = 0
        self.started = asyncio.Event()

    async def process(self, message, state, lease):
        self.calls += 1
        self.started.set()
        await asyncio.sleep(self.delay)
        return ProcessingResult(True, "NO_OP_CHECKPOINT_COMMITTED")


def settings(**changes):
    base = dict(
        aws_region="us-east-1",
        queue_url="https://sqs.invalid/q",
        table_name="campaign-test",
        wait_time_seconds=0,
        batch_size=1,
        visibility_timeout_seconds=2,
        heartbeat_interval_seconds=0.01,
        max_delivery_attempts=5,
        shutdown_grace_seconds=1,
        environment="test",
    )
    base.update(changes)
    return Settings(**base)


@pytest.mark.asyncio
async def test_valid_receive_success_and_durable_ack():
    value = job()
    queue = StubSQS([raw(value)])
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(), "worker-a")
    assert await consumer.run_once() == [MessageOutcome.ACKNOWLEDGED]
    assert repository.completed and queue.deletes == ["opaque-receipt"]
    assert queue.receives == 1


@pytest.mark.parametrize(
    "body",
    ["not-json", json.dumps({"schema_version": 99}), json.dumps({"schema_version": 1})],
)
def test_invalid_json_unknown_schema_and_missing_field(body):
    with pytest.raises(ValueError, match="invalid queue message"):
        SQSConsumer.parse(raw(job(), body=body))


@pytest.mark.asyncio
async def test_invalid_message_remains_for_redrive_and_body_not_logged(caplog):
    secret_body = '{"private_token":"do-not-log"}'
    value = job()
    consumer = SQSConsumer(StubSQS(), FakeRepository(value), NoOpJobProcessor(), settings())
    assert await consumer.process_raw(raw(value, body=secret_body)) == MessageOutcome.INVALID
    assert "do-not-log" not in caplog.text


@pytest.mark.asyncio
async def test_unsupported_schema_version_with_identity_persists_durable_record():
    value = job()
    body = json.dumps({"schema_version": 2, "campaign_id": str(value.campaign_id)})
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings())
    assert await consumer.process_raw(raw(value, body=body)) == MessageOutcome.INVALID
    assert repository.invalid == [(value.campaign_id, "UNSUPPORTED_MESSAGE_SCHEMA")]
    assert queue.deletes == []


@pytest.mark.asyncio
async def test_validation_error_with_identity_persists_durable_record():
    value = job()
    body = json.dumps({"schema_version": 1, "campaign_id": str(value.campaign_id)})
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings())
    assert await consumer.process_raw(raw(value, body=body)) == MessageOutcome.INVALID
    assert repository.invalid == [(value.campaign_id, "VALIDATION_ERROR")]
    assert queue.deletes == []


@pytest.mark.asyncio
async def test_unparseable_message_does_not_invent_identity():
    value = job()
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings())
    assert await consumer.process_raw(raw(value, body="not-json")) == MessageOutcome.INVALID
    assert repository.invalid == []
    assert queue.deletes == []


@pytest.mark.asyncio
async def test_invalid_record_persistence_failure_still_returns_invalid():
    value = job()
    body = json.dumps({"schema_version": 2, "campaign_id": str(value.campaign_id)})
    repository = FakeRepository(value)

    async def fail_record_invalid(campaign_id, code, message_id, now):
        raise PersistenceUnavailable("unavailable")

    repository.record_invalid = fail_record_invalid
    queue = StubSQS()
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings())
    assert await consumer.process_raw(raw(value, body=body)) == MessageOutcome.INVALID
    assert queue.deletes == []


@pytest.mark.asyncio
async def test_lease_conflict_and_different_job_are_not_deleted():
    value = job()
    for delivered in (value, value.model_copy(update={"job_id": uuid4()})):
        queue = StubSQS()
        repository = FakeRepository(value)
        repository.conflict = delivered is value
        consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(), "worker-b")
        assert await consumer.process_raw(raw(delivered)) == MessageOutcome.LEASE_CONFLICT
        assert queue.deletes == []


@pytest.mark.asyncio
async def test_duplicate_completed_job_is_safe_noop_and_same_job_twice():
    value = job()
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(), "worker-a")
    assert await consumer.process_raw(raw(value)) == MessageOutcome.ACKNOWLEDGED
    assert await consumer.process_raw(raw(value, count=2)) == MessageOutcome.ACKNOWLEDGED
    assert len(queue.deletes) == 2


@pytest.mark.asyncio
async def test_expired_lease_can_be_recovered():
    value = job()
    repository = FakeRepository(value)
    repository.lease = LeaseContext("dead-worker", 4, datetime.now(UTC) - timedelta(seconds=1))
    queue = StubSQS()
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(), "replacement")
    assert await consumer.process_raw(raw(value, count=2)) == MessageOutcome.ACKNOWLEDGED


@pytest.mark.asyncio
async def test_visibility_extension_and_transient_failure_recovery():
    value = job()
    queue = StubSQS()
    queue.visibility_failures = 1
    repository = FakeRepository(value)
    processor = SlowProcessor(0.05)
    consumer = SQSConsumer(queue, repository, processor, settings(), "worker-a")
    assert await consumer.process_raw(raw(value)) == MessageOutcome.ACKNOWLEDGED
    assert queue.visibility >= 2 and queue.deletes


@pytest.mark.asyncio
async def test_repeated_visibility_failure_or_lease_loss_prevents_delete():
    value = job()
    for failure in ("visibility", "lease"):
        queue = StubSQS()
        repository = FakeRepository(value)
        if failure == "visibility":
            queue.visibility_failures = 10
        else:
            repository.heartbeat_error = LeaseLost
        consumer = SQSConsumer(queue, repository, SlowProcessor(0.3), settings(), "worker-a")
        assert await consumer.process_raw(raw(value)) == MessageOutcome.UNCERTAIN
        assert queue.deletes == []


@pytest.mark.asyncio
async def test_retry_bound_records_failure_without_delete():
    value = job()
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(max_delivery_attempts=3))
    assert await consumer.process_raw(raw(value, count=4)) == MessageOutcome.RETRY_EXHAUSTED
    assert repository.exhausted == [4] and queue.deletes == []


@pytest.mark.asyncio
async def test_health_is_non_consuming_and_shutdown_stops_receive():
    value = job()
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings())
    assert await consumer.available() and queue.receives == 0
    queue.health = False
    assert not await consumer.available() and queue.receives == 0
    await consumer.shutdown()
    await consumer.run()
    assert queue.receives == 0


def test_settings_validation(monkeypatch):
    settings().validate()
    for value in (
        Settings(),
        settings(batch_size=11),
        settings(wait_time_seconds=21),
        settings(visibility_timeout_seconds=1, heartbeat_interval_seconds=2),
        settings(max_delivery_attempts=0),
        settings(environment="prod", endpoint_url="http://localhost:4566"),
    ):
        with pytest.raises(ConfigurationError):
            value.validate()
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    Settings.from_env().validate()


@pytest.mark.asyncio
async def test_receive_failure_is_sanitized():
    value = job()
    queue = StubSQS()

    def fail_receive(**kwargs):
        raise ClientError({"Error": {"Code": "Denied", "Message": "private body"}}, "ReceiveMessage")

    queue.receive_message = fail_receive
    consumer = SQSConsumer(queue, FakeRepository(value), NoOpJobProcessor(), settings())
    with pytest.raises(ProcessingUncertain, match="queue receive unavailable") as failure:
        await consumer.receive()
    assert "private body" not in str(failure.value)


@pytest.mark.asyncio
async def test_missing_state_processor_noop_and_persistence_uncertainty_do_not_delete():
    value = job()

    class IncompleteProcessor(JobProcessor):
        async def process(self, message, state, lease):
            return ProcessingResult(False, "NOT_COMMITTED")

    cases = []
    repository = FakeRepository(value)

    async def missing(message):
        return None

    repository.load_version = missing
    cases.append((repository, NoOpJobProcessor()))
    cases.append((FakeRepository(value), IncompleteProcessor()))
    repository = FakeRepository(value)

    async def fail_complete(message, lease, completed_at):
        raise PersistenceUnavailable("unavailable")

    repository.complete = fail_complete
    cases.append((repository, NoOpJobProcessor()))
    for repository, processor in cases:
        queue = StubSQS()
        consumer = SQSConsumer(queue, repository, processor, settings())
        assert await consumer.process_raw(raw(value)) == MessageOutcome.UNCERTAIN
        assert queue.deletes == []


@pytest.mark.asyncio
async def test_delete_failure_and_exhaustion_persistence_failure_are_uncertain():
    value = job()
    queue = StubSQS()

    def fail_delete(**kwargs):
        raise ClientError({"Error": {"Code": "Unavailable", "Message": "private"}}, "DeleteMessage")

    queue.delete_message = fail_delete
    consumer = SQSConsumer(queue, FakeRepository(value), NoOpJobProcessor(), settings())
    assert await consumer.process_raw(raw(value)) == MessageOutcome.UNCERTAIN
    repository = FakeRepository(value)

    async def fail_exhausted(message, receive_count, now):
        raise PersistenceUnavailable("unavailable")

    repository.record_exhausted = fail_exhausted
    consumer = SQSConsumer(StubSQS(), repository, NoOpJobProcessor(), settings(max_delivery_attempts=1))
    assert await consumer.process_raw(raw(value, count=2)) == MessageOutcome.UNCERTAIN


@pytest.mark.asyncio
async def test_repository_health_failure_makes_worker_unready():
    value = job()
    repository = FakeRepository(value)
    repository.available_value = False
    queue = StubSQS()
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings())
    assert not await consumer.available()
    assert queue.receives == 0


@pytest.mark.asyncio
async def test_graceful_shutdown_allows_active_completion():
    value = job()
    queue = StubSQS([raw(value)])
    processor = SlowProcessor(0.03)
    consumer = SQSConsumer(queue, FakeRepository(value), processor, settings(shutdown_grace_seconds=0.2))
    running = asyncio.create_task(consumer.run())
    await processor.started.wait()
    await consumer.shutdown()
    await running
    assert queue.deletes == ["opaque-receipt"]


@pytest.mark.asyncio
async def test_shutdown_timeout_cancels_without_acknowledgement():
    value = job()
    queue = StubSQS([raw(value)])
    processor = SlowProcessor(0.2)
    consumer = SQSConsumer(queue, FakeRepository(value), processor, settings(shutdown_grace_seconds=0.005))
    running = asyncio.create_task(consumer.run())
    await processor.started.wait()
    await consumer.shutdown()
    await asyncio.gather(running, return_exceptions=True)
    assert queue.deletes == []
