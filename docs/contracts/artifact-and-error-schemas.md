# Artifact and Error Contracts

## Artifact Reference

Authoritative internal schema:

```json
{
  "schema_version": 1,
  "artifact_id": "uuid",
  "artifact_type": "IMAGE",
  "campaign_id": "uuid",
  "campaign_version": 1,
  "workflow_step": "images",
  "storage_namespace": "campaign-assets",
  "s3_bucket": "resolved-from-server-config",
  "s3_key": "campaigns/<id>/versions/1/images/<artifact-id>.png",
  "mime_type": "image/png",
  "size_bytes": 12345,
  "checksum_sha256": "64 lowercase hex",
  "created_at": "2026-07-28T10:00:00Z",
  "provider": "gemini",
  "provider_artifact_id": null,
  "generation_summary": {
    "aspect_ratio": "9:16",
    "image_size": "1K",
    "quality": "fast",
    "fallback_asset": false
  },
  "presigned_url_expires_at": null
}
```

Rules:

- `artifact_type`: `REFERENCE_IMAGE|IMAGE|AUDIO|VIDEO|STORYBOARD|MANIFEST|REVIEW_PACKAGE|FINAL_PACKAGE|PROVIDER_DIAGNOSTIC`.
- `workflow_step` matches the lifecycle stage that produced/accepted it.
- S3 bucket is resolved by logical namespace in most code; bucket/key are internal and excluded from normal frontend projections.
- S3 key is deterministic, normalized, version-scoped, and cannot contain user path fragments.
- MIME, decoded content, dimensions/duration, size, and checksum are verified before registration.
- `checksum_sha256` is over stored bytes.
- Provider ID is stored only if it is non-secret and required for polling/reconciliation.
- `generation_summary` is allowlisted and excludes full prompts, credentials, private URLs, and raw responses.
- A fallback artifact sets `fallback_asset=true`, records a safe fallback reason in its event/step, and is disclosed to the user.
- Presigned URLs are generated on demand. The URL is never stored; only response-time expiration may be returned. Persistent `presigned_url_expires_at` remains null.
- An artifact record is immutable after acceptance. Replacement creates a new artifact ID.

Public projection omits `s3_bucket`, `s3_key`, provider-private identifiers, and internal generation fields. Image
projections may include `scene_number` and an optional attribution object containing only
`provider_asset_id`, `creator_name`, `creator_profile_url`, `source_page_url`, `provider_url`, and
`attribution_text`. Attribution URLs are HTTPS-only. These optional fields remain null for legacy artifacts. The
voiceover artifact (`artifact_type: "AUDIO"`, `workflow_step: "voiceover"`) never carries `scene_number` or
`attribution` -- a synthesized Polly voice is not a creator/source asset that needs crediting. The rendered campaign
video (`artifact_type: "VIDEO"`, `workflow_step: "video"`) is likewise never attributed -- it is composed locally by
the worker (FFmpeg) from the campaign's own image and voiceover artifacts, not sourced from an external provider.
Video-technical metadata (resolution, duration, codecs, fps, render fingerprint) is recorded only in the private S3
metadata sidecar, never on the public artifact.

Campaign detail returns persisted public image, voiceover, and video references, all without download URLs. The
artifact endpoint may add fresh `download_url` and `download_url_expires_at` values for images, audio, and video.
Image object keys are derived server-side from validated campaign, version, and scene identity; the audio and video
object keys are derived from campaign and version identity alone. Bucket/key are never accepted from the request or
returned publicly. Signed URLs expire within 900 seconds and are never persisted.

## Sanitized Error

```json
{
  "schema_version": 1,
  "code": "IMAGE_PROVIDER_UNAVAILABLE",
  "message": "Image generation is temporarily unavailable.",
  "component": "IMAGE_MCP",
  "workflow_step": "images",
  "attempt": 2,
  "retryable": true,
  "timestamp": "2026-07-28T10:00:00Z",
  "correlation_id": "uuid",
  "campaign_id": "uuid",
  "campaign_version": 1,
  "job_id": "uuid",
  "provider_status": {
    "provider": "gemini",
    "category": "THROTTLED",
    "http_status": 429,
    "provider_code": "RESOURCE_EXHAUSTED"
  }
}
```

### Required Fields

`schema_version`, stable `code`, safe `message`, `component`, nullable `workflow_step`, positive `attempt`, `retryable`, timestamp, correlation ID, campaign ID/version when known.

`component`: `FASTAPI|LANGGRAPH_WORKER|MARKETING_MCP|IMAGE_MCP|HYPERFRAMES_MCP|BEDROCK|DYNAMODB|S3|SQS|PACKAGER|UNKNOWN`.

Provider status is optional and allowlisted: provider name, coarse category, numeric HTTP status, and sanitized short provider code. Never persist provider message/body by default.

### Stable Initial Error Codes

`VALIDATION_ERROR`, `NOT_FOUND`, `STATE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `UNSUPPORTED_MESSAGE_SCHEMA`, `LEASE_CONFLICT`, `RETRY_EXHAUSTED`, `CANCELLED_BY_USER`, `BEDROCK_UNAVAILABLE`, `IMAGE_PROVIDER_UNAVAILABLE`, `VOICE_PROVIDER_UNAVAILABLE`, `VIDEO_PROVIDER_UNAVAILABLE`, `PROVIDER_POLICY_REJECTION`, `PROVIDER_THROTTLED`, `PROVIDER_TIMEOUT`, `INVALID_PROVIDER_OUTPUT`, `ARTIFACT_VALIDATION_FAILED`, `STORAGE_UNAVAILABLE`, `QUEUE_UNAVAILABLE`, `PACKAGE_VALIDATION_FAILED`, and `INTERNAL_ERROR`.

### Redaction Rules

Never store or return:

- Secrets, API keys, OAuth/access/refresh tokens, cookies, signatures, presigned URLs, or SQS receipt handles.
- AWS account IDs, private workspace/space IDs unless operationally required and encrypted/restricted.
- Full private prompts or campaign text inside error records.
- Raw provider response bodies or sensitive headers.
- Local absolute paths, internal hostnames, pod names when user-visible, or stack traces.

Operational logs may contain a protected stack trace keyed by correlation ID, but API and DynamoDB error projections do not.

Messages are written for users and do not reveal internals. Error `code` drives program behavior; message text is not parsed.

## Failure Persistence and Events

The worker writes the error to the VERSION and failed STEP conditionally with status `FAILED`, then emits a `FAILED` event containing only code, component, step, attempt, and retryable flag. If persistence fails, it must not delete the SQS message.

Retry replaces the current version error only with a new attempt state while retaining failure history in append-only events/step attempt metadata. Approval/finalization errors do not erase the immutable approval record.
