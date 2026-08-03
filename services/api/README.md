# Campaign API — Task 8 Boundary

This Python 3.12 FastAPI service implements campaign creation, retrieval, deterministic listing, liveness, readiness, the DynamoDB repository adapter, and an Amazon SQS producer. It does not contain an SQS consumer, worker, LangGraph, Bedrock, or MCP execution.

## Local commands

From the repository root, using Python 3.12:

```bash
python -m venv .venv-api
. .venv-api/bin/activate
python -m pip install -e "shared[dev]"
python -m pip install -e "services/api[dev]"
cd services/api
ruff format --check src tests
ruff check src tests
mypy src
pytest -q --cov=campaign_api --cov-report=term-missing
uvicorn campaign_api.main:app
```

## Queue selection

`QUEUE_BACKEND=memory` is the safe local default. Select `sqs` only with `AWS_REGION` and `SQS_QUEUE_URL`. `SQS_REQUEST_TIMEOUT_SECONDS` controls connect/read deadlines. `SQS_ENDPOINT_URL` is restricted to `local` or `test` environments for LocalStack-style testing. Credentials come from the normal AWS credential chain and must never be placed in environment examples or source files.

The SQS adapter receives an injected low-level client, serializes only the validated shared `SQSJobMessage`, and uses Standard SQS. Readiness calls `GetQueueAttributes`; it never sends or consumes a message. Liveness has no AWS dependency.

## Campaign submission

1. Validate the request and derive stable campaign/job identities from the idempotency key.
2. Atomically persist `META` and `VERSION#1` as `CREATED`.
3. submit the validated `START` message.
4. Conditionally advance both records to `QUEUED`.
5. Return `202` only after both the send acknowledgement and state transition succeed.

DynamoDB and SQS do not share a transaction. A definitive send failure triggers guarded deletion of untouched initial records and returns sanitized `503 QUEUE_UNAVAILABLE`. A read timeout is ambiguous because SQS may have accepted the message; the service preserves `CREATED` plus its stable `job_id`, returns sanitized `503`, and does not automatically resubmit that uncertain command. A failure after SQS acceptance also preserves `CREATED`; reconciliation must inspect the durable version/job identity and safely perform the missing transition. It must never delete the accepted message blindly.

Repeated API requests with the same idempotency key and identical brief return the existing queued campaign without another send. Reuse with a different brief returns `409 IDEMPOTENCY_CONFLICT`. Producer behavior does not replace worker-side lease, checkpoint, and duplicate-delivery protection required by Standard SQS at-least-once delivery.

## Endpoints

- `POST /api/v1/campaigns`
- `GET /api/v1/campaigns/{campaign_id}`
- `GET /api/v1/campaigns`
- `GET /health/live`
- `GET /health/ready`
