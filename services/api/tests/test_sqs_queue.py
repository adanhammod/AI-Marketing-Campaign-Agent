import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from campaign_contracts.enums import SQSOperation
from campaign_contracts.sqs import SQSJobMessage

from campaign_api.config import Settings
from campaign_api.exceptions import QueueSubmissionAmbiguousFailure, QueueSubmissionFailure
from campaign_api.queue.factory import create_job_queue
from campaign_api.queue.job_queue import QueueSubmissionResult
from campaign_api.queue.sqs_job_queue import SQSJobQueue


class StubSQS:
    def __init__(self) -> None:
        self.sent = []
        self.health_calls = []
        self.receive_calls = 0
        self.send_error = None
        self.health_error = None

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        if self.send_error:
            raise self.send_error
        return {"MessageId": "provider-message-id"}

    def get_queue_attributes(self, **kwargs):
        self.health_calls.append(kwargs)
        if self.health_error:
            raise self.health_error
        return {"Attributes": {"ApproximateNumberOfMessages": "0"}}

    def receive_message(self, **kwargs):
        self.receive_calls += 1
        raise AssertionError("readiness must not consume")


def message():
    return SQSJobMessage(
        schema_version=1,
        job_id=uuid4(),
        campaign_id=uuid4(),
        campaign_version=1,
        operation=SQSOperation.START,
        requested_step=None,
        revision_scope=None,
        idempotency_key="task8",
        correlation_id=uuid4(),
        requested_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        attempt=0,
        trace_id="0123456789abcdef0123456789abcdef",
    )


@pytest.mark.asyncio
async def test_exact_deterministic_wire_body_and_injected_client():
    client = StubSQS()
    queue = SQSJobQueue(client, "https://sqs.invalid/private")
    value = message()
    result = await queue.submit(value)
    assert result == QueueSubmissionResult(job_id=value.job_id, accepted=True)
    assert len(client.sent) == 1
    body = client.sent[0]["MessageBody"]
    assert body == SQSJobQueue.serialize(value)
    assert json.loads(body) == value.model_dump(mode="json", by_alias=True)
    assert set(json.loads(body)) == set(type(value).model_fields)
    assert all(term not in body for term in ("business_name", "prompt", "credential", "artifact"))


@pytest.mark.asyncio
async def test_sanitized_send_failure_and_ambiguous_timeout():
    client = StubSQS()
    queue = SQSJobQueue(client, "https://sqs.invalid/private-account/secret")
    client.send_error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "private provider payload"}}, "SendMessage"
    )
    with pytest.raises(QueueSubmissionFailure, match="queue submission failed") as failure:
        await queue.submit(message())
    assert "private" not in str(failure.value)
    client.send_error = ReadTimeoutError(endpoint_url="https://sqs.invalid/private")
    with pytest.raises(QueueSubmissionAmbiguousFailure, match="outcome is unknown"):
        await queue.submit(message())


@pytest.mark.asyncio
async def test_health_is_non_consuming_and_sanitized():
    client = StubSQS()
    queue = SQSJobQueue(client, "https://sqs.invalid/private")
    assert await queue.available()
    assert client.health_calls[0]["AttributeNames"] == ["ApproximateNumberOfMessages"]
    assert client.receive_calls == 0 and client.sent == []
    client.health_error = ClientError({"Error": {"Code": "Denied", "Message": "secret"}}, "GetQueueAttributes")
    assert not await queue.available()


def test_configuration_and_factory(monkeypatch):
    assert Settings().queue_backend == "memory"
    with pytest.raises(ValueError, match="AWS_REGION and SQS_QUEUE_URL"):
        Settings(queue_backend="sqs").validate()
    with pytest.raises(ValueError, match="local testing"):
        Settings(environment="prod", sqs_endpoint_url="http://localstack:4566").validate()
    client = StubSQS()
    settings = Settings(queue_backend="sqs", aws_region="us-east-1", sqs_queue_url="https://sqs.invalid/q")
    queue = create_job_queue(settings, client)
    assert isinstance(queue, SQSJobQueue)
    monkeypatch.setenv("QUEUE_BACKEND", "invalid")
    with pytest.raises(ValueError, match="QUEUE_BACKEND"):
        Settings.from_env()
