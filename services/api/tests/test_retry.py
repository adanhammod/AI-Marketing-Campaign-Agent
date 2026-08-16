import asyncio
from uuid import uuid4, uuid5

import pytest
from campaign_contracts.api import RetryResponse
from campaign_contracts.campaign import RetryMetadata, StrategyOutput
from campaign_contracts.enums import CampaignStatus, SQSOperation, WorkflowStep
from fastapi.testclient import TestClient

from campaign_api.dependencies import get_queue
from campaign_api.exceptions import QueueSubmissionAmbiguousFailure, QueueSubmissionFailure, RepositoryFailure
from campaign_api.queue.in_memory_job_queue import InMemoryJobQueue


def _retry(client, campaign_id, version, headers):
    return client.post(f"/api/v1/campaigns/{campaign_id}/versions/{version}/retry", json={}, headers=headers)


def _retry_job_id(campaign_id, campaign_version, attempt):
    return uuid5(campaign_id, f"RETRY:{campaign_version}:{attempt}")


RETRYABLE_FAILED = RetryMetadata(attempt=1, max_attempts=3, retryable=True, resume_step=WorkflowStep.IMAGES)
NON_RETRYABLE_FAILED = RetryMetadata(attempt=3, max_attempts=3, retryable=False, resume_step=WorkflowStep.VIDEO)
PACKAGE_RETRYABLE_FAILED = RetryMetadata(attempt=1, max_attempts=3, retryable=True, resume_step=WorkflowStep.PACKAGE)

NON_FAILED_STATUSES = [
    CampaignStatus.CREATED,
    CampaignStatus.QUEUED,
    CampaignStatus.GENERATING_STRATEGY,
    CampaignStatus.GENERATING_COPY,
    CampaignStatus.GENERATING_STORYBOARD,
    CampaignStatus.GENERATING_IMAGES,
    CampaignStatus.RENDERING_VIDEO,
    CampaignStatus.READY_FOR_REVIEW,
    CampaignStatus.APPROVED,
    CampaignStatus.FINAL,
    CampaignStatus.REVISION_REQUESTED,
    CampaignStatus.CANCELLED,
]


def test_retry_from_retryable_failed_returns_202_and_transitions_to_queued(
    client, repository, queue, campaign_at_status, headers
):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))

    response = _retry(client, campaign_id, 1, headers)

    assert response.status_code == 202
    body = RetryResponse.model_validate(response.json())
    assert body.status == CampaignStatus.QUEUED
    assert body.resume_step == WorkflowStep.IMAGES
    assert body.attempt == 1
    expected_job_id = _retry_job_id(campaign_id, 1, 1)
    assert body.job_id == expected_job_id
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.QUEUED
    assert record[1].job_id == expected_job_id
    # RetryMetadata is worker-owned; the API must not mutate it.
    assert record[1].retry == RETRYABLE_FAILED
    messages = queue.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.START
    assert messages[0].job_id == expected_job_id
    assert messages[0].requested_step is None
    assert messages[0].revision_scope is None


def test_retry_from_non_retryable_failed_rejected(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=NON_RETRYABLE_FAILED))

    response = _retry(client, campaign_id, 1, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_retry_with_missing_resume_step_is_rejected_without_mutation(
    client, repository, queue, campaign_at_status, headers
):
    """A: a FAILED/retryable version whose resume_step is None -- e.g. a worker
    failure that happened between graph nodes rather than inside one -- must be
    rejected before any mutation. Checking resume_step only after committing the
    FAILED->QUEUED transition (the pre-fix ordering) would leave the campaign stuck
    QUEUED with a new job_id, an advanced lock_version, and a RETRY_SCHEDULED event,
    but no resume point and no SQS message ever submitted."""
    missing_resume_step = RetryMetadata(attempt=1, max_attempts=3, retryable=True, resume_step=None)
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=missing_resume_step))
    before = asyncio.run(repository.get(campaign_id))
    assert before is not None

    response = _retry(client, campaign_id, 1, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"
    after = asyncio.run(repository.get(campaign_id))
    assert after is not None
    before_aggregate, before_version = before
    after_aggregate, after_version = after
    assert after_version.status == CampaignStatus.FAILED
    assert after_version.job_id == before_version.job_id
    assert after_version.lock_version == before_version.lock_version
    assert after_version.retry == before_version.retry
    assert after_aggregate.lock_version == before_aggregate.lock_version
    assert after_aggregate.event_sequence == before_aggregate.event_sequence
    assert queue.messages() == []
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None and record[1].status == CampaignStatus.FAILED


def test_retry_campaign_not_found(client, headers):
    response = _retry(client, uuid4(), 1, headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_retry_stale_version_conflicts(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))

    response = _retry(client, campaign_id, 2, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_retry_identical_replay_returns_original_response(client, repository, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))

    first = _retry(client, campaign_id, 1, headers)

    calls = 0
    original = repository.retry

    async def counting_retry(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    repository.retry = counting_retry
    second = _retry(client, campaign_id, 1, headers)
    repository.retry = original

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert calls == 0  # replay never re-invokes the repository write
    messages = queue.messages()
    assert len(messages) == 1  # deterministic job_id de-duplicates in the fake queue too
    assert messages[0].job_id == _retry_job_id(campaign_id, 1, 1)


def test_retry_replay_with_mismatched_job_id_conflicts(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))
    assert _retry(client, campaign_id, 1, headers).status_code == 202

    # Simulate the QUEUED state having come from something other than this retry attempt.
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    mismatched = version.model_copy(update={"job_id": uuid4(), "lock_version": version.lock_version + 1})
    mismatched_aggregate = aggregate.model_copy(update={"lock_version": aggregate.lock_version + 1})
    asyncio.run(repository.replace_current(mismatched_aggregate, mismatched))

    response = _retry(client, campaign_id, 1, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_retry_sends_start_operation_with_correct_job_id(client, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))

    _retry(client, campaign_id, 1, headers)

    messages = queue.messages()
    assert len(messages) == 1
    message = messages[0]
    assert message.operation == SQSOperation.START
    assert message.job_id == _retry_job_id(campaign_id, 1, 1)
    assert message.campaign_id == campaign_id
    assert message.campaign_version == 1


def test_retry_from_package_resume_step_submits_resume_operation(client, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=PACKAGE_RETRYABLE_FAILED))

    response = _retry(client, campaign_id, 1, headers)

    assert response.status_code == 202
    body = RetryResponse.model_validate(response.json())
    assert body.resume_step == WorkflowStep.PACKAGE
    messages = queue.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.RESUME
    assert messages[0].job_id == _retry_job_id(campaign_id, 1, 1)


def test_retry_from_non_package_resume_step_still_submits_start_operation(client, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))

    _retry(client, campaign_id, 1, headers)

    messages = queue.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.START


def test_retry_vs_cancel_on_failed_campaign_cancel_rejected(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))

    cancel_response = client.post(
        f"/api/v1/campaigns/{campaign_id}/versions/1/cancel", json={"reason": "reason"}, headers=headers
    )
    assert cancel_response.status_code == 409

    retry_response = _retry(client, campaign_id, 1, headers)
    assert retry_response.status_code == 202


class _FailOnceQueue:
    """Wraps a real InMemoryJobQueue but raises on the first submit() call only,
    simulating a definite send failure followed by a successful client retry."""

    def __init__(self, failure: Exception):
        self._inner = InMemoryJobQueue()
        self._failure = failure
        self.calls = 0

    async def submit(self, message):
        self.calls += 1
        if self.calls == 1:
            raise self._failure
        return await self._inner.submit(message)

    async def available(self):
        return True

    def messages(self):
        return self._inner.messages()


def test_retry_queue_failure_then_replay_succeeds(app, repository, campaign_at_status, headers):
    fail_once = _FailOnceQueue(QueueSubmissionFailure("simulated failure"))
    app.dependency_overrides[get_queue] = lambda: fail_once
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))
    test_client = TestClient(app, raise_server_exceptions=False)

    first = _retry(test_client, campaign_id, 1, headers)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.QUEUED  # durable, no rollback
    assert record[1].job_id == _retry_job_id(campaign_id, 1, 1)

    second = _retry(test_client, campaign_id, 1, headers)
    assert second.status_code == 202
    messages = fail_once.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.START


def test_retry_ambiguous_queue_failure_then_replay_succeeds(app, repository, campaign_at_status, headers):
    fail_once = _FailOnceQueue(QueueSubmissionAmbiguousFailure("outcome unknown"))
    app.dependency_overrides[get_queue] = lambda: fail_once
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))
    test_client = TestClient(app, raise_server_exceptions=False)

    first = _retry(test_client, campaign_id, 1, headers)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.QUEUED

    second = _retry(test_client, campaign_id, 1, headers)
    assert second.status_code == 202
    assert len(fail_once.messages()) == 1


def test_retry_persistence_failure_returns_503(app, repository, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=RETRYABLE_FAILED))
    original = repository.retry

    async def fail_retry(*args, **kwargs):
        raise RepositoryFailure("simulated persistence failure")

    repository.retry = fail_retry
    test_client = TestClient(app, raise_server_exceptions=False)
    response = _retry(test_client, campaign_id, 1, headers)
    repository.retry = original

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert queue.messages() == []


def test_retry_preserves_generated_artifacts_and_resume_step(client, repository, campaign_at_status, headers):
    strategy = StrategyOutput(
        audience="Professionals",
        positioning="Premium",
        objective="Drive signups",
        key_message="Try it now",
        channel_rationale={"instagram": "high engagement"},
    )
    retry_meta = RetryMetadata(attempt=1, max_attempts=3, retryable=True, resume_step=WorkflowStep.IMAGES)
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.FAILED, retry=retry_meta, strategy=strategy, progress_percent=50, completed_steps=[]
        )
    )

    response = _retry(client, campaign_id, 1, headers)

    assert response.status_code == 202
    body = RetryResponse.model_validate(response.json())
    assert body.resume_step == WorkflowStep.IMAGES
    assert body.attempt == 1
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].strategy == strategy
    assert record[1].progress_percent == 50  # unchanged, matches C2's preservation convention
    assert record[1].retry == retry_meta  # RetryMetadata is worker-owned; API leaves it untouched


@pytest.mark.parametrize("status", NON_FAILED_STATUSES)
def test_retry_from_every_non_failed_status_rejected(client, campaign_at_status, headers, status):
    campaign_id, _ = asyncio.run(campaign_at_status(status))

    response = _retry(client, campaign_id, 1, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"
