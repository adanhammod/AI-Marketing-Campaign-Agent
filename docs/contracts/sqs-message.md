# SQS Workflow Message Contract

## Version 1 Schema

```json
{
  "schema_version": 1,
  "job_id": "018f4f2f-8dc8-7a32-b21a-b857d560c821",
  "campaign_id": "018f4f2f-6f0b-7d0a-a498-6ecb369be5af",
  "campaign_version": 1,
  "operation": "START",
  "requested_step": null,
  "revision_scope": null,
  "idempotency_key": "create-fixture-001",
  "correlation_id": "018f4f2f-9f69-76ef-8394-33501e9e672a",
  "requested_at": "2026-07-28T10:00:00Z",
  "attempt": 0,
  "trace_id": "0123456789abcdef0123456789abcdef"
}
```

`trace_id` is the only field added to the required envelope because it correlates API, queue, worker, provider, and persistence telemetry. Content, approval details, and checkpoint payloads are loaded from DynamoDB.
## Validation

| Field | Rule |
|---|---|
| `schema_version` | Integer exactly `1`. |
| `job_id` | UUID; unique logical command ID. |
| `campaign_id` | UUID matching an existing aggregate. |
| `campaign_version` | Integer >=1 and must exist/current where the operation requires. |
| `operation` | `START`, `RESUME`, or `REGENERATE`. |
| `requested_step` | Nullable workflow-step enum; required for targeted resume/regeneration and otherwise null. |
| `revision_scope` | Nullable `STRATEGY|COPY|STORYBOARD|SELECTED_IMAGES|VIDEO`; allowed only for `REGENERATE`. |
| `idempotency_key` | Required 1-128 printable characters; persisted as a hash. |
| `correlation_id` | UUID propagated through API, persistence, events, and logs. |
| `requested_at` | UTC RFC 3339/ISO-8601 timestamp; reject unreasonable future skew. |
| `attempt` | Integer >=0 representing the business attempt, not SQS receive count. |
| `trace_id` | 32 lowercase hexadecimal characters; optional at compatibility boundary, generated if absent. |
Unknown fields are rejected: the schema is closed within schema version 1 (the executable contract enforces `extra="forbid"`). Introducing a new envelope field requires an explicit `schema_version` increment, not silent forward-compatible addition. Missing/invalid required fields are non-retryable poison messages.

Operation rules:

- `START`: command metadata must describe a newly created version; by consumption time the worker requires that version to be `QUEUED`. No human approval step exists: once a `START`/`REGENERATE` run reaches `READY_FOR_REVIEW`, the worker continues automatically, in the same processing pass, straight through packaging to `FINAL`.
- `RESUME`: version must be `FAILED` with `retry.retryable=true` and `resume_step=PACKAGE`, or legacy `APPROVED` awaiting package finalization. `RESUME` is not part of the normal completion path anymore -- it is used only for retrying a failed packaging attempt, or as a manual escape hatch for a version that was already `READY_FOR_REVIEW`/`APPROVED` before this behavior shipped.
- `REGENERATE`: version must be a new child in `QUEUED` with revision metadata and earliest affected step. Also reaches `FINAL` automatically, same as `START`.

The API sets `CREATED -> QUEUED` when durable send succeeds. Therefore workers normally observe `QUEUED`; operation preconditions are verified against persisted command metadata, not trusted from the message alone.

## Idempotency and Duplicate Delivery

The idempotency boundary is `(campaign_id, campaign_version, operation, idempotency_key)`, with `job_id` identifying the logical command. Repeated delivery of the same body returns the recorded result. Reusing either key with a different request hash is `IDEMPOTENCY_CONFLICT`.

Workers:

1. Validate the envelope.
2. Load META, VERSION, and relevant STEP entities.
3. Acquire a conditional version lease.
4. If the job is already durably completed, delete the duplicate.
5. Resume the first incomplete applicable step.
6. Check step idempotency/output checksum before every provider side effect.
7. Persist checkpoint/status/event before deleting the SQS message.

At-least-once delivery must never create duplicate accepted artifacts or a second approval/final package.

## Queue Assumptions

The MVP uses an SQS Standard queue, not FIFO. There is no `MessageGroupId` ordering guarantee. DynamoDB current-version checks, leases, status preconditions, and optimistic locks provide serialization. Moving to FIFO is optional and would use `campaign_id` as message group ID.

## Visibility and Heartbeats

- Initial visibility timeout: 180 seconds.
- Worker extends visibility before half the remaining timeout elapses while it holds a valid lease.
- Lease duration must be shorter than visibility timeout and renewed with the heartbeat.
- A single provider call must have an application deadline below the maximum visibility-extension window.
- If heartbeat/extension fails, stop starting new side effects, checkpoint when safe, and allow redelivery.
- Never delete before durable checkpoint/status/event commit.

## Failure Classification

Retryable: throttling, provider 429/5xx, bounded network timeout, temporary DNS/TLS failure, S3/SQS/DynamoDB transient error, worker termination, and HyperFrames `rendering` before deadline.

Non-retryable: schema/validation failure, unsupported operation/version, illegal lifecycle transition, missing campaign/version, provider policy rejection, invalid/corrupt artifact after repair limit, cancellation, and exhausted retry budget.

Retries use exponential backoff with full jitter. Step attempts are capped at three unless a contract explicitly sets a lower cap. SQS receive count is not the business attempt count.

## DLQ

The encrypted DLQ receives messages after configured maximum receives (recommended 5) or explicit poison-message routing. Before routing, record a sanitized failure/event when campaign identity is valid. Alarm on any DLQ message and oldest-message age.

Redrive is manual after root-cause correction. It preserves the original body and `job_id`; do not manufacture a new logical command to hide the failure.

Unknown `schema_version` is never retried indefinitely: record `UNSUPPORTED_MESSAGE_SCHEMA` when safe, send to DLQ, and do not mutate workflow output.

## Why Content Is Excluded

SQS is delivery, not state. Full campaign content would exceed practical message size, leak sensitive briefs into queue payloads/logs, become stale, complicate immutable versioning, and create two sources of truth. The message contains identity and intent only; DynamoDB supplies authoritative state and S3 supplies large/binary content.
