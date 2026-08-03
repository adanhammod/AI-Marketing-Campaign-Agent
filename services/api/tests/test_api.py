from typing import get_type_hints
from uuid import UUID, uuid4

from campaign_contracts.api import (
    CampaignCreationAcceptedResponse,
    CampaignCreationRequest,
    CampaignDetailResponse,
    CampaignListResponse,
)
from campaign_contracts.enums import CampaignStatus, SQSOperation
from fastapi.testclient import TestClient

from campaign_api.exceptions import QueueSubmissionAmbiguousFailure, RepositoryFailure


def test_create_returns_202_and_queues_one(client, repository, queue, valid_request, headers):
    response = client.post("/api/v1/campaigns", json=valid_request, headers=headers)
    assert response.status_code == 202
    body = CampaignCreationAcceptedResponse.model_validate(response.json())
    assert body.campaign_version == 1 and body.status == CampaignStatus.QUEUED
    record = __import__("asyncio").run(repository.get(body.campaign_id))
    assert record is not None and record[1].campaign_version == 1 and record[1].status == CampaignStatus.QUEUED
    messages = queue.messages()
    assert (
        len(messages) == 1
        and messages[0].operation == SQSOperation.START
        and messages[0].campaign_id == body.campaign_id
    )
    assert response.headers["X-Request-ID"] == headers["X-Request-ID"]


def test_invalid_request_uses_sanitized_error(client, headers):
    response = client.post("/api/v1/campaigns", json={"business_name": "x"}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "brief" not in response.text


def test_queue_failure_rolls_back_and_never_returns_202(client, repository, queue, valid_request, headers):
    queue.fail_submissions = True
    response = client.post("/api/v1/campaigns", json=valid_request, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    assert __import__("asyncio").run(repository.list(offset=0, limit=10)) == []


def test_get_existing_and_missing(client, valid_request, headers):
    created = client.post("/api/v1/campaigns", json=valid_request, headers=headers).json()
    found = client.get(f"/api/v1/campaigns/{created['campaign_id']}")
    assert found.status_code == 200
    detail = CampaignDetailResponse.model_validate(found.json())
    assert detail.campaign_version == 1 and detail.status == CampaignStatus.QUEUED
    assert not {"lease", "checkpoint", "provider_job_ids", "idempotency_keys", "s3_bucket", "s3_key"}.intersection(
        found.json()
    )
    missing = client.get(f"/api/v1/campaigns/{uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_list_empty_and_deterministic(client, valid_request, headers):
    empty = client.get("/api/v1/campaigns")
    assert empty.status_code == 200 and empty.json() == {"items": [], "next_cursor": None}
    first = client.post("/api/v1/campaigns", json=valid_request, headers=headers).json()
    second = client.post(
        "/api/v1/campaigns", json=valid_request, headers={**headers, "Idempotency-Key": "second"}
    ).json()
    listed = CampaignListResponse.model_validate(client.get("/api/v1/campaigns").json())
    assert [str(x.campaign_id) for x in listed.items] == [second["campaign_id"], first["campaign_id"]]


def test_invalid_incoming_request_id_is_replaced(client):
    response = client.get("/health/live", headers={"X-Request-ID": "not-a-uuid"})
    assert response.status_code == 200
    UUID(response.headers["X-Request-ID"])


def test_shared_contract_models_are_route_models(app):
    included = next(r for r in app.routes if type(r).__name__ == "_IncludedRouter")
    route = next(
        r
        for r in included.original_router.routes
        if getattr(r, "path", None) == "/campaigns" and "POST" in getattr(r, "methods", set())
    )
    assert route.response_model is CampaignCreationAcceptedResponse
    assert get_type_hints(route.endpoint)["body"] is CampaignCreationRequest


def test_same_idempotency_key_returns_original_campaign_without_second_message(
    client, repository, queue, valid_request, headers
):
    first = client.post("/api/v1/campaigns", json=valid_request, headers=headers)
    second = client.post("/api/v1/campaigns", json=valid_request, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["campaign_id"] == second.json()["campaign_id"]
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(queue.messages()) == 1
    assert len(__import__("asyncio").run(repository.list(offset=0, limit=10))) == 1


def test_idempotency_key_reuse_with_different_request_conflicts(client, valid_request, headers):
    assert client.post("/api/v1/campaigns", json=valid_request, headers=headers).status_code == 202
    changed = {**valid_request, "tone": "formal"}
    response = client.post("/api/v1/campaigns", json=changed, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_ambiguous_submission_preserves_created_state(app, repository, valid_request, headers):
    class AmbiguousQueue:
        async def submit(self, message):
            raise QueueSubmissionAmbiguousFailure("queue submission outcome is unknown")

        async def available(self):
            return True

    from campaign_api.dependencies import get_queue

    app.dependency_overrides[get_queue] = lambda: AmbiguousQueue()
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/campaigns", json=valid_request, headers=headers
    )
    assert response.status_code == 503 and response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    records = __import__("asyncio").run(repository.list(offset=0, limit=10))
    assert len(records) == 1 and records[0][1].status == CampaignStatus.CREATED


def test_transition_failure_after_send_preserves_created_state(app, repository, queue, valid_request, headers):
    original = repository.replace_current

    async def fail_transition(*args, **kwargs):
        raise RepositoryFailure("campaign state update failed")

    repository.replace_current = fail_transition
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/campaigns", json=valid_request, headers=headers
    )
    repository.replace_current = original
    assert response.status_code == 503 and response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    records = __import__("asyncio").run(repository.list(offset=0, limit=10))
    assert len(queue.messages()) == 1
    assert len(records) == 1 and records[0][1].status == CampaignStatus.CREATED
