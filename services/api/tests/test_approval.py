import asyncio
from uuid import uuid4, uuid5

from campaign_contracts.api import ApprovalResponse
from campaign_contracts.enums import CampaignStatus, SQSOperation
from fastapi.testclient import TestClient

from campaign_api.dependencies import get_queue
from campaign_api.exceptions import QueueSubmissionAmbiguousFailure, QueueSubmissionFailure, RepositoryFailure
from campaign_api.queue.in_memory_job_queue import InMemoryJobQueue


def _approve(client, campaign_id, version, checksum, headers, note=None):
    return client.post(
        f"/api/v1/campaigns/{campaign_id}/versions/{version}/approve",
        json={"review_manifest_checksum": checksum, "note": note},
        headers=headers,
    )


def test_campaign_detail_exposes_the_review_manifest_checksum_needed_to_approve(
    client, ready_for_review_campaign, approval_checksum
):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    response = client.get(f"/api/v1/campaigns/{campaign_id}")

    assert response.status_code == 200
    assert response.json()["review_manifest_checksum"] == approval_checksum


def test_approve_returns_202_and_transitions_to_approved(
    client, repository, queue, ready_for_review_campaign, approval_checksum, headers
):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    response = _approve(client, campaign_id, 1, approval_checksum, headers)

    assert response.status_code == 202
    body = ApprovalResponse.model_validate(response.json())
    assert body.campaign_id == campaign_id
    assert body.campaign_version == 1
    assert body.status == CampaignStatus.APPROVED
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    _, version = record
    assert version.status == CampaignStatus.APPROVED
    assert version.approval is not None
    assert version.approval.manifest_checksum == approval_checksum
    messages = queue.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.RESUME


def test_approve_sets_version_job_id_to_resume_job_id(
    client, repository, queue, ready_for_review_campaign, approval_checksum, headers
):
    campaign_id, start_job_id = asyncio.run(ready_for_review_campaign())

    response = _approve(client, campaign_id, 1, approval_checksum, headers)

    resume_job_id = uuid5(campaign_id, "RESUME:1")
    assert response.status_code == 202
    assert response.json()["job_id"] == str(resume_job_id)
    assert resume_job_id != start_job_id
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].job_id == resume_job_id
    assert queue.messages()[0].job_id == resume_job_id


def test_approve_rejects_wrong_lifecycle_state(client, queue, valid_request, headers, approval_checksum):
    created = client.post("/api/v1/campaigns", json=valid_request, headers=headers).json()

    response = _approve(client, created["campaign_id"], 1, approval_checksum, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"
    assert len(queue.messages()) == 1  # only the original START


def test_approve_checksum_mismatch_conflicts(client, repository, queue, ready_for_review_campaign, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    response = _approve(client, campaign_id, 1, "b" * 64, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"
    assert queue.messages() == []
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.READY_FOR_REVIEW


def test_approve_campaign_not_found(client, headers, approval_checksum):
    response = _approve(client, uuid4(), 1, approval_checksum, headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_approve_stale_version_conflicts(client, ready_for_review_campaign, approval_checksum, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    response = _approve(client, campaign_id, 2, approval_checksum, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_approve_replay_resubmits_and_stays_idempotent(
    client, queue, ready_for_review_campaign, approval_checksum, headers
):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    first = _approve(client, campaign_id, 1, approval_checksum, headers)
    second = _approve(client, campaign_id, 1, approval_checksum, headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["approval_id"] == second.json()["approval_id"]
    assert first.json()["job_id"] == second.json()["job_id"]
    messages = queue.messages()
    assert len(messages) == 1  # deterministic job_id de-duplicates in the fake queue too
    assert messages[0].job_id == uuid5(campaign_id, "RESUME:1")


def test_approve_replay_with_different_body_conflicts(client, ready_for_review_campaign, approval_checksum, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())
    assert _approve(client, campaign_id, 1, approval_checksum, headers).status_code == 202

    response = _approve(client, campaign_id, 1, "b" * 64, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


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


def test_approve_queue_failure_then_retry_succeeds(
    app, repository, ready_for_review_campaign, approval_checksum, headers
):
    fail_once = _FailOnceQueue(QueueSubmissionFailure("simulated failure"))
    app.dependency_overrides[get_queue] = lambda: fail_once
    campaign_id, _ = asyncio.run(ready_for_review_campaign())
    test_client = TestClient(app, raise_server_exceptions=False)

    first = _approve(test_client, campaign_id, 1, approval_checksum, headers)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.APPROVED  # durable, no rollback
    assert record[1].approval is not None

    second = _approve(test_client, campaign_id, 1, approval_checksum, headers)
    assert second.status_code == 202
    messages = fail_once.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.RESUME


def test_approve_ambiguous_queue_failure_then_retry_succeeds(
    app, repository, ready_for_review_campaign, approval_checksum, headers
):
    fail_once = _FailOnceQueue(QueueSubmissionAmbiguousFailure("outcome unknown"))
    app.dependency_overrides[get_queue] = lambda: fail_once
    campaign_id, _ = asyncio.run(ready_for_review_campaign())
    test_client = TestClient(app, raise_server_exceptions=False)

    first = _approve(test_client, campaign_id, 1, approval_checksum, headers)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.APPROVED

    second = _approve(test_client, campaign_id, 1, approval_checksum, headers)
    assert second.status_code == 202
    assert len(fail_once.messages()) == 1


def test_approve_submits_resume_message(client, queue, ready_for_review_campaign, approval_checksum, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    _approve(client, campaign_id, 1, approval_checksum, headers)

    messages = queue.messages()
    assert len(messages) == 1
    message = messages[0]
    assert message.operation == SQSOperation.RESUME
    assert message.campaign_id == campaign_id
    assert message.campaign_version == 1
    assert message.requested_step is None
    assert message.revision_scope is None


def test_approve_never_sends_start_or_regenerate(client, queue, ready_for_review_campaign, approval_checksum, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    _approve(client, campaign_id, 1, approval_checksum, headers)
    _approve(client, campaign_id, 1, approval_checksum, headers)

    operations = {m.operation for m in queue.messages()}
    assert operations == {SQSOperation.RESUME}


def test_approve_persistence_failure_returns_503(
    app, repository, queue, ready_for_review_campaign, approval_checksum, headers
):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())
    original = repository.approve

    async def fail_approve(*args, **kwargs):
        raise RepositoryFailure("simulated persistence failure")

    repository.approve = fail_approve
    test_client = TestClient(app, raise_server_exceptions=False)
    response = _approve(test_client, campaign_id, 1, approval_checksum, headers)
    repository.approve = original

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert queue.messages() == []
