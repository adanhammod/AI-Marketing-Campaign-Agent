import asyncio
from uuid import uuid4

from campaign_contracts.campaign import RetryMetadata, ReviewPackage
from campaign_contracts.enums import CampaignStatus, WorkflowStep


def _events(client, campaign_id, cursor=None, limit=None):
    params = {}
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = limit
    return client.get(f"/api/v1/campaigns/{campaign_id}/events", params=params)


def _approve(client, campaign_id, version, checksum, headers, note=None):
    return client.post(
        f"/api/v1/campaigns/{campaign_id}/versions/{version}/approve",
        json={"review_manifest_checksum": checksum, "note": note},
        headers=headers,
    )


def _cancel(client, campaign_id, version, reason, headers):
    return client.post(
        f"/api/v1/campaigns/{campaign_id}/versions/{version}/cancel", json={"reason": reason}, headers=headers
    )


def _retry(client, campaign_id, version, headers):
    return client.post(f"/api/v1/campaigns/{campaign_id}/versions/{version}/retry", json={}, headers=headers)


def _revise(client, campaign_id, version, headers, reason="Make the CTA more direct", scope="COPY"):
    body = {"reason": reason, "scope": scope, "affected_artifact_ids": []}
    return client.post(f"/api/v1/campaigns/{campaign_id}/versions/{version}/revisions", json=body, headers=headers)


# ---------------------------------------------------------------------------
# Emission: one event per API-owned lifecycle mutation
# ---------------------------------------------------------------------------


def test_create_emits_campaign_created(client, valid_request, headers):
    created = client.post("/api/v1/campaigns", json=valid_request, headers=headers).json()

    body = _events(client, created["campaign_id"]).json()

    assert [e["event_type"] for e in body["items"]] == ["CAMPAIGN_CREATED"]
    assert body["items"][0]["event_sequence"] == 1
    assert body["latest_sequence"] == 1


def test_approve_emits_approved(client, ready_for_review_campaign, approval_checksum, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    response = _approve(client, campaign_id, 1, approval_checksum, headers)
    assert response.status_code == 202

    body = _events(client, campaign_id).json()
    assert [e["event_type"] for e in body["items"]] == ["APPROVED"]
    assert body["latest_sequence"] == 1


def test_cancel_immediate_emits_cancel_requested_and_cancelled(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))

    response = _cancel(client, campaign_id, 1, "no longer needed", headers)
    assert response.status_code == 200

    body = _events(client, campaign_id).json()
    assert [e["event_type"] for e in body["items"]] == ["CANCEL_REQUESTED", "CANCELLED"]
    assert [e["event_sequence"] for e in body["items"]] == [1, 2]
    assert body["latest_sequence"] == 2


def test_cancel_pending_emits_only_cancel_requested(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.GENERATING_IMAGES))

    response = _cancel(client, campaign_id, 1, "no longer needed", headers)
    assert response.status_code == 202

    body = _events(client, campaign_id).json()
    assert [e["event_type"] for e in body["items"]] == ["CANCEL_REQUESTED"]
    assert body["latest_sequence"] == 1


def test_retry_emits_retry_scheduled(client, campaign_at_status, headers):
    retry_meta = RetryMetadata(attempt=1, max_attempts=3, retryable=True, resume_step=WorkflowStep.IMAGES)
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=retry_meta))

    response = _retry(client, campaign_id, 1, headers)
    assert response.status_code == 202

    body = _events(client, campaign_id).json()
    assert [e["event_type"] for e in body["items"]] == ["RETRY_SCHEDULED"]
    assert body["latest_sequence"] == 1


def test_revision_emits_revision_requested(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))

    response = _revise(client, campaign_id, 1, headers)
    assert response.status_code == 202

    body = _events(client, campaign_id).json()
    assert [e["event_type"] for e in body["items"]] == ["REVISION_REQUESTED"]
    assert body["items"][0]["campaign_version"] == 1  # describes the frozen parent, not the new child
    assert body["latest_sequence"] == 1


# ---------------------------------------------------------------------------
# event_sequence rules
# ---------------------------------------------------------------------------


def test_event_sequence_continues_monotonically_and_gaplessly_from_existing_value(
    client, repository, campaign_at_status, approval_checksum, headers
):
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.READY_FOR_REVIEW,
            review_package=ReviewPackage(
                artifact_id=uuid4(), manifest_checksum=approval_checksum, artifact_ids=[uuid4()]
            ),
        )
    )
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    bumped_aggregate = aggregate.model_copy(update={"event_sequence": 5})
    asyncio.run(repository.replace_current(bumped_aggregate, version))

    response = _approve(client, campaign_id, 1, approval_checksum, headers)
    assert response.status_code == 202

    body = _events(client, campaign_id).json()
    assert body["items"][0]["event_sequence"] == 6  # continues from 5, no gap, no reset to 1
    assert body["latest_sequence"] == 6


# ---------------------------------------------------------------------------
# Replay must not duplicate events or re-increment event_sequence
# ---------------------------------------------------------------------------


def test_approve_replay_does_not_duplicate_event(client, ready_for_review_campaign, approval_checksum, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())
    _approve(client, campaign_id, 1, approval_checksum, headers)

    _approve(client, campaign_id, 1, approval_checksum, headers)

    body = _events(client, campaign_id).json()
    assert len(body["items"]) == 1
    assert body["latest_sequence"] == 1


def test_cancel_replay_does_not_duplicate_event(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    _cancel(client, campaign_id, 1, "no longer needed", headers)

    _cancel(client, campaign_id, 1, "no longer needed", headers)

    body = _events(client, campaign_id).json()
    assert len(body["items"]) == 2  # CANCEL_REQUESTED + CANCELLED, still only once
    assert body["latest_sequence"] == 2


def test_retry_replay_does_not_duplicate_event(client, campaign_at_status, headers):
    retry_meta = RetryMetadata(attempt=1, max_attempts=3, retryable=True, resume_step=WorkflowStep.IMAGES)
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FAILED, retry=retry_meta))
    _retry(client, campaign_id, 1, headers)

    _retry(client, campaign_id, 1, headers)

    body = _events(client, campaign_id).json()
    assert len(body["items"]) == 1
    assert body["latest_sequence"] == 1


def test_revision_replay_does_not_duplicate_event(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    _revise(client, campaign_id, 1, headers, reason="same reason", scope="COPY")

    _revise(client, campaign_id, 1, headers, reason="same reason", scope="COPY")

    body = _events(client, campaign_id).json()
    assert len(body["items"]) == 1
    assert body["latest_sequence"] == 1


def test_failed_mutation_does_not_append_event(client, ready_for_review_campaign, headers):
    campaign_id, _ = asyncio.run(ready_for_review_campaign())

    response = _approve(client, campaign_id, 1, "b" * 64, headers)  # wrong checksum
    assert response.status_code == 409

    body = _events(client, campaign_id).json()
    assert body["items"] == []
    assert body["latest_sequence"] == 0


# ---------------------------------------------------------------------------
# GET /events read semantics
# ---------------------------------------------------------------------------


def test_events_returns_ascending_order(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.GENERATING_IMAGES, event_count=4))

    body = _events(client, campaign_id).json()

    sequences = [e["event_sequence"] for e in body["items"]]
    assert sequences == sorted(sequences)
    assert sequences == [1, 2, 3, 4]


def test_events_pagination_and_next_cursor(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.GENERATING_IMAGES, event_count=5))

    first_page = _events(client, campaign_id, limit=2).json()
    assert [e["event_sequence"] for e in first_page["items"]] == [1, 2]
    assert first_page["next_cursor"] == "2"

    second_page = _events(client, campaign_id, cursor=first_page["next_cursor"], limit=2).json()
    assert [e["event_sequence"] for e in second_page["items"]] == [3, 4]
    assert second_page["next_cursor"] == "4"

    third_page = _events(client, campaign_id, cursor=second_page["next_cursor"], limit=2).json()
    assert [e["event_sequence"] for e in third_page["items"]] == [5]
    assert third_page["next_cursor"] is None


def test_events_invalid_cursor_returns_400(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))

    response = _events(client, campaign_id, cursor="not-a-number")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_events_unknown_campaign_returns_404(client):
    response = _events(client, uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_events_latest_sequence_and_terminal_correct(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.CANCELLED, event_count=3))

    body = _events(client, campaign_id).json()

    assert body["latest_sequence"] == 3
    assert body["terminal"] is True


def test_events_non_terminal_status_reports_terminal_false(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.GENERATING_IMAGES, event_count=1))

    body = _events(client, campaign_id).json()

    assert body["terminal"] is False


def test_events_empty_state_for_fresh_campaign_is_supported(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.CREATED))

    body = _events(client, campaign_id).json()

    assert body["items"] == []
    assert body["next_cursor"] is None
    assert body["latest_sequence"] == 0


def test_events_duplicate_event_ids_not_returned_twice(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.GENERATING_IMAGES, event_count=1))
    stored_event = asyncio.run(repository.list_events(campaign_id, 0, 10))[0]
    # Simulate a duplicate write of the exact same event (same event_id) landing twice --
    # production code never does this (replay paths skip the write entirely), but the
    # read path must still defend against it per the append-only contract's dedup rule.
    repository._events[campaign_id].append(stored_event.model_copy())  # noqa: SLF001

    body = _events(client, campaign_id).json()

    assert len(body["items"]) == 1
