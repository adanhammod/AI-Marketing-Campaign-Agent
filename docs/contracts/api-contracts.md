# HTTP API Contracts

Base path: `/api/v1`. JSON uses `snake_case`, timestamps are UTC ISO-8601, IDs are UUIDs, and unknown request fields are rejected. Mutation endpoints require `Idempotency-Key` (1–128 printable characters). The server stores only its hash.

All errors use the shared error envelope:

```json
{
  "error": {
    "code": "STATE_CONFLICT",
    "message": "Campaign version is not ready for this action.",
    "component": "FASTAPI",
    "step": null,
    "attempt": 1,
    "retryable": false,
    "timestamp": "2026-07-28T10:00:00Z",
    "correlation_id": "uuid",
    "job_id": null,
    "provider_status": null,
    "fields": []
  }
}
```

Common behavior: malformed JSON `400`; schema errors `422`; unknown campaign `404`; illegal state or idempotency hash mismatch `409`; oversized request `413`; dependency unavailable before durable acceptance `503`. Replaying the same idempotency key and request returns the original response.

## Shared Projections

### Campaign Summary

```json
{
  "campaign_id": "uuid",
  "title": "Northwind launch",
  "current_version": 1,
  "latest_final_version": null,
  "status": "QUEUED",
  "current_step": "strategy",
  "progress_percent": 2,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

### Campaign Detail

Extends summary with normalized `brief`, `constraints`, exact current `version`, nullable `strategy`, `copy`, `storyboard`, public artifact summaries, safe `revision`, safe `approval`, `completed_steps`, `retry_eligible`, sanitized `error`, `event_sequence`, and action links. It never contains S3 bucket/key, lease data, receipt handles, raw provider responses, full private prompt history, or stored presigned URLs.

## `POST /campaigns`

Request:

```json
{
  "business_name": "Northwind Coffee",
  "product_or_service": "Cold brew subscription",
  "business_description": "A local roaster offering weekly delivery.",
  "campaign_goal": "increase_sales",
  "target_audience": "Professionals aged 25-40",
  "platforms": ["instagram"],
  "tone": "energetic",
  "language": "en-US",
  "key_message": null,
  "call_to_action": null,
  "brand_colors": ["#4B2E2A"],
  "reference_artifact_id": null
}
```

Success: `202 Accepted`

```json
{
  "campaign_id": "uuid",
  "campaign_version": 1,
  "job_id": "uuid",
  "status": "QUEUED",
  "progress_percent": 2,
  "links": {"self": "/api/v1/campaigns/uuid", "events": "/api/v1/campaigns/uuid/events"}
}
```

The request is accepted only after campaign/version persistence and durable SQS send. If send fails, the API either safely retries within its bound or returns `503` with the version left `CREATED` and recoverable; it never claims `QUEUED`.

## `GET /campaigns`

Query: `limit` 1–100 (default 20), opaque `cursor`, optional `status`.

Success `200`:

```json
{"items": [], "next_cursor": null}
```

Invalid cursor/filter: `400`. Read-only and not idempotency-keyed.

## `GET /campaigns/{campaign_id}`

Optional query `version`; default current version.

Success `200`: Campaign Detail. Unknown aggregate/version: `404`. Invalid UUID/version: `422`.

## `GET /campaigns/{campaign_id}/events`

Query: opaque `cursor`, `limit` 1–100 (default 50).

Success `200`:

```json
{
  "campaign_id": "uuid",
  "campaign_version": 1,
  "items": [],
  "next_cursor": null,
  "latest_sequence": 12,
  "terminal": false
}
```

Events are ascending by sequence. Unknown campaign: `404`; invalid cursor: `400`. Duplicate event IDs are not returned twice.

## `GET /campaigns/{campaign_id}/artifacts`

Query: optional `version` (default current), optional `type`.

Success `200`:

```json
{
  "campaign_id": "uuid",
  "campaign_version": 1,
  "items": [
    {
      "artifact_id": "uuid",
      "artifact_type": "IMAGE",
      "workflow_step": "images",
      "mime_type": "image/png",
      "size_bytes": 12345,
      "checksum_sha256": "64 lowercase hex",
      "created_at": "ISO-8601",
      "provider": "pexels",
      "scene_number": 1,
      "attribution": {
        "provider_asset_id": "123456",
        "creator_name": "Photographer Name",
        "creator_profile_url": "https://www.pexels.com/@photographer/",
        "source_page_url": "https://www.pexels.com/photo/123456/",
        "provider_url": "https://www.pexels.com/",
        "attribution_text": "Photo by Photographer Name on Pexels"
      },
      "download_url": "https://short-lived-signed-url",
      "download_url_expires_at": "ISO-8601"
    }
  ]
}
```

Presigned image and audio URLs are generated on demand, expire within 900 seconds, and are never persisted. The API
derives the private object identity from campaign/version (plus scene, for images) and never exposes S3 bucket or
key. The voiceover artifact (`artifact_type: "AUDIO"`) is signed the same way but carries no `scene_number` or
`attribution`. Existing video references are included but are not assigned a download URL. Legacy artifacts without
scene or attribution remain valid with null optional fields. Unknown campaign/version: `404`; unsupported type: `422`.

## `POST /campaigns/{campaign_id}/versions/{version}/approve`

Request:

```json
{"review_manifest_checksum": "64 lowercase hex", "note": null}
```

Preconditions: exact version is current and `READY_FOR_REVIEW`; manifest checksum matches; no approval exists.

Success `202`:

```json
{"campaign_id": "uuid", "campaign_version": 1, "approval_id": "uuid", "status": "APPROVED", "job_id": "uuid"}
```

FastAPI transactionally creates approval and status/event, then queues `RESUME` for final packaging. Unknown: `404`; stale/non-current/checksum mismatch/illegal status: `409`; invalid note/checksum: `422`.

## `POST /campaigns/{campaign_id}/versions/{version}/revisions`

Request:

```json
{
  "campaign_version": 1,
  "reason": "Make the CTA more direct",
  "scope": "COPY",
  "affected_artifact_ids": []
}
```

`scope`: `STRATEGY|COPY|STORYBOARD|SELECTED_IMAGES|VIDEO`. Selected images require valid affected image IDs; other scopes reject them.

Precondition: exact current version is `READY_FOR_REVIEW`. Success `202`:

```json
{
  "campaign_id": "uuid",
  "parent_version": 1,
  "campaign_version": 2,
  "job_id": "uuid",
  "status": "QUEUED",
  "earliest_affected_step": "copy"
}
```

Transaction freezes parent as `REVISION_REQUESTED`, creates child, advances META pointer, and queues `REGENERATE`. Unknown: `404`; stale/illegal/concurrent revision: `409`; invalid scope/feedback/artifacts: `422`.

## `POST /campaigns/{campaign_id}/versions/{version}/retry`

Request:

```json
{}
```

Preconditions: exact current version is `FAILED`, error is retryable, attempt budget remains, and no active lease/job.

Success `202`:

```json
{"campaign_id": "uuid", "campaign_version": 1, "job_id": "uuid", "status": "QUEUED", "resume_step": "images", "attempt": 2}
```

Unknown: `404`; non-retryable/exhausted/stale/active: `409`; rate-limited user retry: `429`.

## `POST /campaigns/{campaign_id}/versions/{version}/cancel`

Request:

```json
{"reason": "User stopped generation"}
```

Allowed for current `CREATED`, `QUEUED`, and generation states. `READY_FOR_REVIEW` may be cancelled if the user chooses not to proceed. `APPROVED`, `FINAL`, `REVISION_REQUESTED`, and already terminal versions reject cancellation.

Success:

- `200 OK` with `status=CANCELLED` when no external call is active.
- `202 Accepted` with `cancellation_pending=true` when a worker/provider call must reach a safe boundary.

Unknown: `404`; terminal/stale version: `409`; invalid reason: `422`.

## Polling Behavior

React polls `GET /campaigns/{campaign_id}` every 2–5 seconds and may fetch events after `event_sequence`. It stops normal polling at `FINAL`, `REVISION_REQUESTED`, `FAILED`, or `CANCELLED`; at `READY_FOR_REVIEW` it switches to slow/manual refresh while awaiting user action. A higher `current_version` causes navigation/refresh to that version. Clients must not derive legal actions independently; use returned action links and status.
