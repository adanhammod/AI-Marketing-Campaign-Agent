# Campaign Worker — Task 9 Boundary

This Python 3.12 service implements only SQS receipt/validation, DynamoDB processing leases, visibility heartbeats, durable transport completion markers, duplicate handling, retry-bound recording, acknowledgement, readiness, and graceful shutdown. The processor is an explicit no-op boundary. LangGraph and all AI/media work remain deferred.

## Polling and configuration

The worker uses an injected low-level SQS client with bounded batches (1–10), long polling (0–20 seconds), the frozen initial visibility timeout, and `ApproximateReceiveCount` only as an approximate delivery counter. It never trusts message attributes as campaign data and never logs message bodies. AWS clients are created only by the composition root.

See `.env.example`. The visibility timeout must exceed the heartbeat interval. Every process instance generates its own worker identity. `AWS_ENDPOINT_URL` is accepted only in local/test environments.

## Processing boundary

1. Parse JSON and validate the complete shared `SQSJobMessage`.
2. Load the exact campaign version.
3. Conditionally acquire its lease using owner, expiry, job ID, operation, and lock version.
4. Check the frozen `(campaign_id, campaign_version, operation, idempotency_key)` completion marker.
5. Run the Task 9 no-op processor.
6. Atomically write an `IDEMPOTENCY#WORKER#...` completion marker, increment the checkpoint version, and release the lease.
7. Delete the SQS message only after that durable commit.

The no-op marker proves the transport boundary only; it is not campaign-generation completion and does not change campaign status. The later workflow task must explicitly version or replace this placeholder processor behavior before processing real campaign jobs.

## Duplicate rules

- Same `job_id`, already completed: acquire safely, confirm marker, release, acknowledge without processing.
- Same `job_id`, active foreign lease: do not process or delete.
- Different `job_id` for the version/operation: lease condition rejects it; do not delete.
- Duplicate after lease expiry: a new worker may acquire and resume.
- Unsupported schema, invalid JSON, or missing fields: do not delete; SQS redrive handles the DLQ. When the raw payload is valid JSON and a well-formed `campaign_id` can be safely extracted, a best-effort durable `UNSUPPORTED_MESSAGE_SCHEMA` (unsupported `schema_version`) or `VALIDATION_ERROR` (other validation failure) record is written, keyed by the SQS transport `MessageId`. An unparseable body or one without an extractable `campaign_id` is left for redrive without inventing an identity, and a failure while writing this diagnostic record never changes the outcome or blocks redrive.
- Standard SQS remains at-least-once; producer and consumer safeguards do not claim transport deduplication.

## Visibility, failures, and shutdown

Visibility and DynamoDB lease heartbeats run together. One transient visibility/persistence failure is retried at the next bounded interval; repeated failure or lease loss stops extension and prevents acknowledgement. Uncertain processing, persistence failure, lease conflict, and delete failure leave the message for redelivery. The application processing bound must be lower than the SQS redrive `maxReceiveCount` (MVP defaults: 4 and 5). When the approximate receive count exceeds the application bound, a sanitized durable failure marker is written and the message remains for the queue redrive policy.

Shutdown stops new receives, gives the active message `WORKER_SHUTDOWN_GRACE_SECONDS`, then cancels it if necessary. Cancellation stops its heartbeat and never acknowledges uncertain work; the lease eventually expires.

Readiness calls SQS queue attributes and DynamoDB table description without receiving or sending messages. No HTTP endpoint is required for Task 9.

## Local verification

```bash
python -m pip install -e "shared[dev]"
python -m pip install -e "services/worker[dev]"
ruff format --check services/worker
ruff check services/worker
mypy services/worker/src
pytest services/worker/tests -q --cov=services/worker/src/campaign_worker --cov-fail-under=90
```
