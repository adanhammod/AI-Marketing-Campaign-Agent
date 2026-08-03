# Campaign State and DynamoDB Contract

This document defines the language-neutral shared state, immutable version rules, DynamoDB entities, and MVP access patterns.

## Shared Campaign-Version State

```json
{
  "schema_version": 1,
  "campaign_id": "uuid",
  "campaign_version": 1,
  "parent_version": null,
  "job_id": "uuid",
  "status": "QUEUED",
  "current_step": "strategy",
  "progress_percent": 2,
  "brief": {},
  "constraints": {},
  "strategy": null,
  "copy": null,
  "storyboard": null,
  "image_prompts": [],
  "image_artifacts": [],
  "video_artifact": null,
  "review_package_artifact": null,
  "revision": null,
  "approval": null,
  "completed_steps": [],
  "retry": {},
  "error": null,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

### Required Fields

`schema_version`, `campaign_id`, `campaign_version`, `job_id`, `status`, `current_step`, `progress_percent`, `brief`, `constraints`, `completed_steps`, `retry`, `created_at`, and `updated_at`.

The normalized brief contains `business_name`, `product_or_service`, `business_description`, `campaign_goal`, `platforms`, `tone`, `language`, optional `target_audience`, `key_message`, `call_to_action`, `brand_colors`, and optional reference-artifact ID. Constraints contain three images, three scenes, target duration 15 seconds, accepted duration 13–17 seconds, `9:16`, preferred 1080×1920, fallback 720×1280, and output MP4/H.264/AAC.

### Optional Generated Fields

`strategy`, `copy`, `storyboard`, `image_prompts`, `image_artifacts`, `video_artifact`, `review_package_artifact`, `revision`, `approval`, and `error` are absent or null until valid.

- Strategy: audience, positioning, objective, message, channel rationale.
- Copy: headline, caption, CTA, hashtags, and channel variants.
- Storyboard: exactly three ordered scenes with purpose, timing, narration, overlay, visual prompt, and transition.
- Revision: parent version, safe user feedback, reason, affected artifact IDs, and earliest affected step.
- Approval: approval ID, exact version, approved timestamp, and optional safe note.

### Internal Worker Fields

Not exposed in normal API projections: lease owner/expiry, SQS receipt metadata, provider request/job IDs, checkpoint payload/version, raw retry scheduling, idempotency keys, internal diagnostics, prompt/template versions, and model/tool configuration snapshots.

### User-Visible Fields

Campaign/version identifiers, status, current step, progress, brief projection, generated outputs, artifact references without S3 bucket/key, safe revision/approval summaries, completed steps, retry eligibility, sanitized error, timestamps, and action links.

Large provider payloads, binaries, full prompt transcripts, manifests, and package bodies are never embedded in DynamoDB/API state. They use artifact references to S3.

## Campaign Aggregate

The aggregate is the stable user-visible campaign:

```json
{
  "campaign_id": "uuid",
  "current_version": 2,
  "latest_final_version": 1,
  "title": "derived safe title",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "lock_version": 4
}
```

Only the aggregate’s pointers and summary may change. A version contains the immutable brief snapshot and outputs for one attempt.

## Immutable Version Rules

- Version numbers begin at 1 and increase by exactly one.
- Only the current version may be approved, revised, retried, or cancelled.
- `parent_version` is required for version 2+ and must reference the immediately preceding reviewed version.
- A version in `READY_FOR_REVIEW` may receive only approval/revision/cancellation metadata and legal status changes; generated output is frozen.
- `REVISION_REQUESTED`, `FINAL`, and `CANCELLED` versions are immutable.
- `FAILED` may be requeued only without changing committed outputs; a content change requires a new version.
- Approval is a separate entity bound to `(campaign_id, campaign_version)` and cannot be copied to a child.
- Child creation and aggregate `current_version` update occur transactionally.

## Regeneration Matrix

| Feedback | Earliest Affected Step | Reuse |
|---|---|---|
| Strategy | `strategy` | Brief/constraints only |
| Copy | `copy` | Strategy |
| Storyboard | `storyboard` | Strategy and copy |
| Selected images | `images` | Strategy, copy, storyboard, unaffected valid images |
| Video only | `video` | Strategy, copy, storyboard, all valid images |

Reused outputs are copied by immutable reference/checksum into the child version and recorded with `STEP_REUSED`. An artifact’s original version remains unchanged; a child-version reference may point to it.

## DynamoDB Single-Table Entities

All entities use `PK=CAMPAIGN#<campaign_id>`. `lock_version` is an integer incremented by every mutation.

### `SK=META`

Required: `entity_type=CAMPAIGN`, `campaign_id`, `current_version`, `title`, `created_at`, `updated_at`, `lock_version`, `event_sequence`.

Optional: `latest_final_version`, `current_status`, `current_progress`, `deleted_at`.

Owner: FastAPI. Conditional writes require expected `lock_version`; child creation requires `current_version = parent_version`.

No lease, artifact body, error body, or TTL.

### `SK=VERSION#<n>`

Required: `entity_type=CAMPAIGN_VERSION`, IDs, version, `job_id`, status, current step, progress, normalized brief, constraints, completed steps, retry summary, timestamps, `lock_version`, `checkpoint_version`.

Optional: parent/revision/approval references, structured generated summaries, artifact IDs, sanitized error, cancellation request, provider/config summaries, lease fields.

Status owner is defined by the lifecycle contract. Every transition condition checks current status, current version where applicable, expected `lock_version`, and lease owner for worker writes.

Lease fields: `lease_owner`, `lease_acquired_at`, `lease_expires_at`, `lease_heartbeat_at`. Acquisition requires no unexpired lease. Lease expiry is not DynamoDB TTL.

No TTL for campaign versions.

### `SK=STEP#<n>#<step>`

Required: `entity_type=WORKFLOW_STEP`, version, step, status (`PENDING|RUNNING|SUCCEEDED|FAILED|REUSED|CANCELLED`), attempt, idempotency key, created/updated timestamps, `lock_version`.

Optional: input/output checksums, checkpoint S3 reference, artifact IDs, provider/model/tool names, safe generation-parameter summary, start/end time, sanitized error, lease snapshot.

Worker owns step status. Completion requires expected attempt/idempotency key and active version lease. A succeeded/reused step cannot return to running in the same version.

TTL is allowed only for explicitly ephemeral diagnostic payload references, never the step entity.

### `SK=EVENT#<zero-padded-sequence>#<event_id>`

Required: `entity_type=CAMPAIGN_EVENT` and every field in the event contract.

Append-only. Transactionally increment `META.event_sequence`; duplicate `event_id` is ignored. No update/delete and no TTL during MVP retention.

### `SK=APPROVAL#<version>`

Required: `entity_type=APPROVAL`, approval ID, campaign/version, `decision=APPROVED`, approved timestamp, actor `DEMO_USER`, approved artifact-manifest checksum, created timestamp, `lock_version=1`.

Optional: safe note.

FastAPI owns creation. Condition: version is current and `READY_FOR_REVIEW`; approval entity does not exist; manifest checksum matches. Immutable; no TTL.

## Additional Supporting Entities

Artifact metadata uses `SK=ARTIFACT#<version>#<artifact_id>` and the artifact contract. An idempotency record may use `SK=IDEMPOTENCY#<scope>#<key_hash>` with request hash, response projection, created time, and TTL of at least 24 hours. TTL is permitted only on idempotency/ephemeral markers.

## Sanitized Error Storage

Only the shared error contract is stored. Provider status is allowlisted and truncated. Stack traces and raw provider bodies belong only in protected operational logs when necessary, never in DynamoDB user state.

## MVP Access Patterns

| Access Pattern | Key/Index |
|---|---|
| Get campaign aggregate | `PK`, `SK=META` |
| Get current version | Read META, then `SK=VERSION#<current_version>` |
| Get exact version | `PK`, exact version SK |
| List versions | Query `PK`, `begins_with(SK,"VERSION#")` |
| Get workflow steps | Query `PK`, `begins_with(SK,"STEP#<n>#")` |
| Poll events after cursor | Query `PK`, `SK > EVENT#<sequence>` with event prefix/range |
| Get approval | Exact `APPROVAL#<version>` |
| List artifacts for version | Query `PK`, `begins_with(SK,"ARTIFACT#<n>#")` |
| List campaigns newest first | `GSI1PK=CAMPAIGNS`, `GSI1SK=<created_at>#<campaign_id>` on META |
| Find campaigns by current status | Optional `GSI2PK=STATUS#<status>`, `GSI2SK=<updated_at>#<campaign_id>` on META |

The single-user MVP does not use an owner partition. Pagination cursors are opaque encoded key maps.
