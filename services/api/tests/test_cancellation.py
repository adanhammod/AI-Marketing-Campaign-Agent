import asyncio
from uuid import uuid4

import pytest
from campaign_contracts.api import CancellationResponse
from campaign_contracts.campaign import StrategyOutput
from campaign_contracts.enums import CampaignStatus
from fastapi.testclient import TestClient

from campaign_api.exceptions import RepositoryFailure


def _cancel(client, campaign_id, version, reason, headers):
    return client.post(
        f"/api/v1/campaigns/{campaign_id}/versions/{version}/cancel",
        json={"reason": reason},
        headers=headers,
    )


IMMEDIATE_STATUSES = [CampaignStatus.CREATED, CampaignStatus.QUEUED, CampaignStatus.READY_FOR_REVIEW]
PENDING_STATUSES = [
    CampaignStatus.GENERATING_STRATEGY,
    CampaignStatus.GENERATING_COPY,
    CampaignStatus.GENERATING_STORYBOARD,
    CampaignStatus.GENERATING_IMAGES,
    CampaignStatus.RENDERING_VIDEO,
]
NOT_CANCELLABLE_STATUSES = [
    CampaignStatus.APPROVED,
    CampaignStatus.FINAL,
    CampaignStatus.FAILED,
    CampaignStatus.REVISION_REQUESTED,
]


@pytest.mark.parametrize("status", IMMEDIATE_STATUSES)
def test_cancel_from_immediate_status_returns_200(client, repository, campaign_at_status, headers, status):
    campaign_id, _ = asyncio.run(campaign_at_status(status))

    response = _cancel(client, campaign_id, 1, "User stopped generation", headers)

    assert response.status_code == 200
    body = CancellationResponse.model_validate(response.json())
    assert body.status == CampaignStatus.CANCELLED and body.cancellation_pending is False
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.CANCELLED
    assert record[1].cancellation_reason == "User stopped generation"
    assert record[1].cancelled_at is not None


@pytest.mark.parametrize("status", PENDING_STATUSES)
def test_cancel_from_active_generation_status_returns_202_pending(
    client, repository, campaign_at_status, headers, status
):
    campaign_id, _ = asyncio.run(campaign_at_status(status))

    response = _cancel(client, campaign_id, 1, "User stopped generation", headers)

    assert response.status_code == 202
    body = CancellationResponse.model_validate(response.json())
    assert body.status == CampaignStatus.CANCELLED and body.cancellation_pending is True
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None and record[1].status == CampaignStatus.CANCELLED


@pytest.mark.parametrize("status", NOT_CANCELLABLE_STATUSES)
def test_cancel_from_non_cancellable_status_rejected(client, repository, campaign_at_status, headers, status):
    campaign_id, _ = asyncio.run(campaign_at_status(status))

    response = _cancel(client, campaign_id, 1, "User stopped generation", headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None and record[1].status == status


def test_cancel_campaign_not_found(client, headers):
    response = _cancel(client, uuid4(), 1, "reason", headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_cancel_stale_version_conflicts(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.QUEUED))

    response = _cancel(client, campaign_id, 2, "reason", headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_cancel_identical_replay_returns_original_response(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.QUEUED))

    first = _cancel(client, campaign_id, 1, "User stopped generation", headers)
    original_cancelled_at = first.json()["status"]

    calls = 0
    original = repository.cancel

    async def counting_cancel(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    repository.cancel = counting_cancel
    second = _cancel(client, campaign_id, 1, "User stopped generation", headers)
    repository.cancel = original

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert original_cancelled_at == "CANCELLED"
    assert calls == 0  # replay never re-invokes the repository write


def test_cancel_replay_with_different_reason_conflicts(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.QUEUED))
    assert _cancel(client, campaign_id, 1, "User stopped generation", headers).status_code == 200

    response = _cancel(client, campaign_id, 1, "Changed my mind for a different reason", headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_cancel_never_touches_queue(client, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.GENERATING_IMAGES))

    _cancel(client, campaign_id, 1, "reason", headers)

    assert queue.messages() == []


def test_cancel_preserves_generated_artifacts(client, repository, campaign_at_status, headers):
    strategy = StrategyOutput(
        audience="Professionals",
        positioning="Premium",
        objective="Drive signups",
        key_message="Try it now",
        channel_rationale={"instagram": "high engagement"},
    )
    campaign_id, _ = asyncio.run(
        campaign_at_status(CampaignStatus.GENERATING_IMAGES, strategy=strategy, progress_percent=42)
    )

    response = _cancel(client, campaign_id, 1, "reason", headers)

    assert response.status_code == 202
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].strategy == strategy
    assert record[1].progress_percent == 42  # unchanged, matches cancelled-campaign.json fixture convention


def test_cancel_persistence_failure_returns_503(app, repository, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.QUEUED))
    original = repository.cancel

    async def fail_cancel(*args, **kwargs):
        raise RepositoryFailure("simulated persistence failure")

    repository.cancel = fail_cancel
    test_client = TestClient(app, raise_server_exceptions=False)
    response = _cancel(test_client, campaign_id, 1, "reason", headers)
    repository.cancel = original

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert queue.messages() == []
