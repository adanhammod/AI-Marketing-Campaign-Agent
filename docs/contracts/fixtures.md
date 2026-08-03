# Initial Contract Fixtures

> **Superseded.** These early, language-neutral examples predate the frozen Pydantic models and no longer match their field names (e.g. this file's `request.product_name`/`objective` vs. the real `NormalizedCampaignBrief.brief.business_name`/`campaign_goal`; its nested `payload.from_status` vs. the real flat `CampaignEvent.status`/`step`; its `category`/`stage` vs. the real `SanitizedWorkflowError.component`/`workflow_step`). The authoritative, contract-tested fixtures are `shared/fixtures/valid/*.json` and `shared/fixtures/invalid/*.json`, validated against `shared/src/campaign_contracts/` in `shared/tests/`. This file is retained only for historical reference and must not be used as a source of truth.

These examples are language-neutral test fixtures. Implementation in Task 5 may copy them into the shared schema package after machine-readable schemas are created. UUIDs, keys, URLs, and timestamps are synthetic.

## Queued Campaign Version

```json
{
  "campaign_id": "018f0000-0000-7000-8000-000000000001",
  "version": 1,
  "status": "QUEUED",
  "progress_percent": 2,
  "current_step": null,
  "request": {
    "product_name": "Example Coffee",
    "objective": "Launch a summer cold brew",
    "target_audience": "Urban professionals aged 22-35",
    "tone": "bright and confident",
    "platforms": ["instagram"]
  },
  "created_at": "2026-07-28T09:00:00Z",
  "updated_at": "2026-07-28T09:00:01Z"
}
```

## Start Message

```json
{
  "schema_version": 1,
  "job_id": "018f0000-0000-7000-8000-000000000003",
  "campaign_id": "018f0000-0000-7000-8000-000000000001",
  "campaign_version": 1,
  "operation": "START",
  "requested_step": null,
  "revision_scope": null,
  "idempotency_key": "fixture-create-001",
  "correlation_id": "018f0000-0000-7000-8000-000000000004",
  "trace_id": "0123456789abcdef0123456789abcdef",
  "requested_at": "2026-07-28T09:00:01Z",
  "attempt": 0
}
```

## Ordered Status Event

```json
{
  "event_id": "018f0000-0000-7000-8000-000000000005",
  "campaign_id": "018f0000-0000-7000-8000-000000000001",
  "campaign_version": 1,
  "event_sequence": 3,
  "event_type": "STATUS_CHANGED",
  "occurred_at": "2026-07-28T09:00:05Z",
  "actor_type": "WORKER",
  "correlation_id": "018f0000-0000-7000-8000-000000000004",
  "payload": {
    "from_status": "QUEUED",
    "to_status": "GENERATING_STRATEGY",
    "progress_percent": 5
  }
}
```

## Sanitized Provider Timeout

```json
{
  "error": {
    "code": "PROVIDER_TIMEOUT",
    "message": "The video provider did not complete within the allowed time.",
    "category": "EXTERNAL_PROVIDER",
    "retryable": true,
    "campaign_id": "018f0000-0000-7000-8000-000000000001",
    "campaign_version": 1,
    "stage": "RENDERING_VIDEO",
    "correlation_id": "018f0000-0000-7000-8000-000000000004",
    "occurred_at": "2026-07-28T09:10:05Z",
    "details": {
      "provider": "video-provider",
      "timeout_seconds": 600
    }
  }
}
```

## Fixture Assertions

- Every example validates against its corresponding frozen contract.
- The queue fixture contains identifiers/control metadata only.
- The event fixture is sortable by `event_sequence`.
- The error contains no token, account/workspace ID, private URL, local path, raw provider body, or stack trace.
- Reusing the same operation and idempotency key must return the original result without a second side effect.