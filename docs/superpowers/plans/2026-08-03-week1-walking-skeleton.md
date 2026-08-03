# Week 1 Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the campaign-generation pipeline mechanics end-to-end (state loading, deterministic text nodes, targeted-regeneration skip/reuse, cancellation checkpoints) on top of the already-built Tasks 6-9 boundary, without calling any real external AI/media provider.

**Architecture:** LangGraph runs as a stateless per-invocation node executor with no native checkpointer; DynamoDB (via the worker's own repository) remains the sole durability source for step-level bookkeeping. Six deterministic text nodes run inside `services/worker`. Marketing MCP is scaffolded as its own service package but is not yet wired to the worker over the network. A provider abstraction layer with deterministic mock Image/Video providers is built standalone, ready for real-provider wiring in a later week.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI (services/api, untouched this week except repository wiring), `langgraph` (new dependency in services/worker), `mcp` Python SDK (new dependency in the new services/marketing-mcp package), boto3/moto for DynamoDB, pytest/pytest-asyncio/pytest-cov, ruff, mypy --strict.

## Global Constraints

- Contract-first: `shared/src/campaign_contracts/` is the single source of truth; additions must be backward-compatible (no existing field renamed/removed/repurposed).
- DynamoDB is the only workflow source of truth. No LangGraph native checkpointer or `interrupt()` persistence is used anywhere in this plan.
- Marketing MCP is a separate service (`services/marketing-mcp/`), not in-process with the worker. It is scaffolded this week; the worker does not call it over the network yet (explicit Week 1 boundary, documented in Task 9 and Task 6).
- Walking Skeleton first: nodes are deterministic (no Bedrock, no Image MCP, no HyperFrames calls) so the pipeline mechanics can be proven without live providers.
- Provider abstraction layer: `ImageProvider`/`VideoProvider` interfaces with deterministic mock implementations only; no real provider integration this week.
- No breaking changes to `docs/contracts/api-contracts.md`'s 9 frozen endpoints, the DynamoDB single-table key scheme, or the SQS message envelope.
- Python 3.12 only, ruff line-length 120, ruff rules `E,F,I,B,UP,ASYNC`, `mypy --strict`, `pytest` with `asyncio_mode = "auto"`, coverage `fail_under = 90` per service (`campaign_api`, `campaign_worker`, and the new `campaign_marketing_mcp`).
- Do not implement Bedrock, Image Generator MCP, HyperFrames MCP, Terraform, Kubernetes manifests, or React. Do not implement any Week 2+ scope (real provider wiring, Marketing MCP's real DynamoDB/S3 backing, network integration between the worker and Marketing MCP).

---

## File Structure

```
shared/src/campaign_contracts/
  steps.py                          # NEW: WorkflowStepRecord model
  dynamodb.py                       # MODIFY: serialize_step takes a WorkflowStepRecord
  __init__.py                       # MODIFY: export steps.py

services/api/src/campaign_api/
  config.py                         # MODIFY: add repository_backend field
  repositories/factory.py           # MODIFY: add create_repository()
  repositories/dynamodb_campaign_repository.py  # MODIFY: delete dead lease methods
  main.py                           # MODIFY: use create_repository()
  .env.example                      # MODIFY: document REPOSITORY_BACKEND

services/worker/src/campaign_worker/
  logging.py                        # MODIFY: structured JSON fields via `extra`
  consumer/sqs_consumer.py          # MODIFY: pass structured `extra=` to log calls
  repositories/workflow_repository.py       # MODIFY: add get_step/save_step
  repositories/dynamodb_workflow_repository.py  # MODIFY: implement get_step/save_step
  graph/__init__.py                 # NEW
  graph/state.py                    # NEW: GraphState TypedDict
  graph/boundary.py                 # NEW: step-tracking + cancellation wrappers
  graph/nodes.py                    # NEW: six deterministic nodes
  graph/executor.py                 # NEW: build_graph() + GraphExecutor
  graph/job_processor.py            # NEW: GraphJobProcessor (JobProcessor impl)
  providers/__init__.py             # NEW
  providers/base.py                 # NEW: ImageProvider/VideoProvider ABCs
  providers/mock_image_provider.py  # NEW: MockImageProvider
  providers/mock_video_provider.py  # NEW: MockVideoProvider
  main.py                           # MODIFY: wire GraphJobProcessor (feature-flagged)
  pyproject.toml                    # MODIFY: add langgraph dependency

services/marketing-mcp/             # NEW service package
  pyproject.toml
  .env.example
  src/campaign_marketing_mcp/__init__.py
  src/campaign_marketing_mcp/service.py   # MarketingMCPService (8 tools, in-memory store)
  src/campaign_marketing_mcp/server.py    # FastMCP tool wiring
  tests/test_service.py
```

---

### Task 1: Wire REPOSITORY_BACKEND into the FastAPI application

**Explanation:** `services/api/src/campaign_api/repositories/factory.py` already has a tested `create_dynamodb_repository()`, but `main.create_app()` never calls it — the API always defaults to `InMemoryCampaignRepository`, so campaign data does not survive a pod restart. This task adds an env-driven `REPOSITORY_BACKEND=memory|dynamodb` switch mirroring the existing `QUEUE_BACKEND` pattern.

**Files:**
- Modify: `services/api/src/campaign_api/config.py`
- Modify: `services/api/src/campaign_api/repositories/factory.py`
- Modify: `services/api/src/campaign_api/main.py`
- Modify: `services/api/.env.example`
- Test: `services/api/tests/test_repository_backend.py` (new)

**Interfaces:**
- Consumes: `Settings` dataclass (`services/api/src/campaign_api/config.py`), `DynamoDBCampaignRepository`, `InMemoryCampaignRepository`.
- Produces: `create_repository(settings: Settings, client: Any | None = None) -> CampaignRepository` in `campaign_api.repositories.factory`, used by `main.create_app()`.

- [ ] **Step 1: Write the failing test**

```python
# services/api/tests/test_repository_backend.py
import pytest

from campaign_api.config import Settings
from campaign_api.repositories.dynamodb_campaign_repository import DynamoDBCampaignRepository
from campaign_api.repositories.factory import create_repository
from campaign_api.repositories.in_memory_campaign_repository import InMemoryCampaignRepository


def test_create_repository_defaults_to_memory():
    repository = create_repository(Settings())
    assert isinstance(repository, InMemoryCampaignRepository)


def test_create_repository_selects_dynamodb_with_injected_client():
    settings = Settings(repository_backend="dynamodb", dynamodb_table_name="campaign-agent-local")
    repository = create_repository(settings, client=object())
    assert isinstance(repository, DynamoDBCampaignRepository)


def test_create_repository_rejects_unknown_backend():
    with pytest.raises(ValueError, match="REPOSITORY_BACKEND"):
        create_repository(Settings(repository_backend="bogus"))


def test_settings_from_env_reads_repository_backend(monkeypatch):
    monkeypatch.setenv("REPOSITORY_BACKEND", "dynamodb")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://example.invalid/queue")
    settings = Settings.from_env()
    assert settings.repository_backend == "dynamodb"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/api && uv run pytest tests/test_repository_backend.py -v`
Expected: FAIL — `TypeError: Settings.__init__() got an unexpected keyword argument 'repository_backend'` and `ImportError: cannot import name 'create_repository'`.

- [ ] **Step 3: Write minimal implementation**

In `services/api/src/campaign_api/config.py`, add the field, extend `validate()`, and extend `from_env()`:

```python
@dataclass(frozen=True, slots=True)
class Settings:
    service_name: str = "campaign-api"
    environment: str = "local"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    max_page_size: int = 100
    dynamodb_table_name: str = "campaign-agent-local"
    repository_backend: str = "memory"
    queue_backend: str = "memory"
    aws_region: str | None = None
    sqs_queue_url: str | None = None
    sqs_request_timeout_seconds: float = 10.0
    sqs_endpoint_url: str | None = None

    def validate(self) -> None:
        if self.repository_backend not in {"memory", "dynamodb"}:
            raise ValueError("REPOSITORY_BACKEND must be memory or dynamodb")
        if self.queue_backend not in {"memory", "sqs"}:
            raise ValueError("QUEUE_BACKEND must be memory or sqs")
        if self.sqs_request_timeout_seconds <= 0:
            raise ValueError("SQS_REQUEST_TIMEOUT_SECONDS must be positive")
        if self.queue_backend == "sqs" and (not self.aws_region or not self.sqs_queue_url):
            raise ValueError("AWS_REGION and SQS_QUEUE_URL are required when QUEUE_BACKEND=sqs")
        if self.sqs_endpoint_url and self.environment not in {"local", "test"}:
            raise ValueError("SQS_ENDPOINT_URL is allowed only for local testing")

    @classmethod
    def from_env(cls) -> "Settings":
        size = int(os.getenv("MAX_PAGE_SIZE", "100"))
        if not 1 <= size <= 100:
            raise ValueError("MAX_PAGE_SIZE must be between 1 and 100")
        prefix = os.getenv("API_PREFIX", "/api/v1")
        if not prefix.startswith("/"):
            raise ValueError("API_PREFIX must start with /")
        settings = cls(
            service_name=os.getenv("SERVICE_NAME", "campaign-api"),
            environment=os.getenv("ENVIRONMENT", "local"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            api_prefix=prefix.rstrip("/"),
            max_page_size=size,
            dynamodb_table_name=os.getenv("DYNAMODB_TABLE_NAME", "campaign-agent-local"),
            repository_backend=os.getenv("REPOSITORY_BACKEND", "memory").lower(),
            queue_backend=os.getenv("QUEUE_BACKEND", "memory").lower(),
            aws_region=os.getenv("AWS_REGION"),
            sqs_queue_url=os.getenv("SQS_QUEUE_URL"),
            sqs_request_timeout_seconds=float(os.getenv("SQS_REQUEST_TIMEOUT_SECONDS", "10")),
            sqs_endpoint_url=os.getenv("SQS_ENDPOINT_URL"),
        )
        settings.validate()
        return settings
```

In `services/api/src/campaign_api/repositories/factory.py`, add `create_repository`:

```python
from typing import Any

import boto3  # type: ignore[import-untyped]

from campaign_api.config import Settings
from campaign_api.repositories.campaign_repository import CampaignRepository
from campaign_api.repositories.dynamodb_campaign_repository import DynamoDBCampaignRepository
from campaign_api.repositories.in_memory_campaign_repository import InMemoryCampaignRepository


def create_dynamodb_repository(client: Any, settings: Settings) -> DynamoDBCampaignRepository:
    return DynamoDBCampaignRepository(client, settings.dynamodb_table_name)


def create_repository(settings: Settings, client: Any | None = None) -> CampaignRepository:
    settings.validate()
    if settings.repository_backend == "memory":
        return InMemoryCampaignRepository()
    resolved_client = client or boto3.client("dynamodb", region_name=settings.aws_region)
    return create_dynamodb_repository(resolved_client, settings)
```

In `services/api/src/campaign_api/main.py`, replace the hardcoded default:

```python
from campaign_api.repositories.factory import create_repository
```

and change:

```python
    app.state.repository = repository or InMemoryCampaignRepository()
```

to:

```python
    app.state.repository = repository or create_repository(resolved)
```

(Remove the now-unused `InMemoryCampaignRepository` import from `main.py` if `create_repository` is the only remaining reference.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/api && uv run pytest tests/test_repository_backend.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full existing API suite to confirm no regression**

Run: `cd services/api && uv run pytest -q`
Expected: all existing tests still PASS (in-memory default behavior unchanged for tests using injected repositories via `create_app(settings, repository, queue)`).

- [ ] **Step 6: Update `.env.example`**

Add a `REPOSITORY_BACKEND=memory` line next to the existing `QUEUE_BACKEND=memory` entry in `services/api/.env.example`, with a one-line comment matching the file's existing comment style for `QUEUE_BACKEND`.

- [ ] **Step 7: Format, lint, type-check, coverage**

Run: `cd services/api && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_api --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 8: Commit**

```bash
git add services/api/src/campaign_api/config.py services/api/src/campaign_api/repositories/factory.py services/api/src/campaign_api/main.py services/api/.env.example services/api/tests/test_repository_backend.py
git commit -m "feat(api): wire REPOSITORY_BACKEND into the FastAPI composition root"
```

---

### Task 2: Fix worker structured logging

**Explanation:** `campaign_worker.logging.JsonFormatter` only serializes `record.getMessage()` as a flat string, so `correlation_id`/`campaign_id`/`job_id` passed via `%s` formatting in `sqs_consumer.py` never become separate JSON fields — silently violating the structured-logging requirement. This task makes the formatter emit any extra fields passed via the standard `logging` `extra=` mechanism as top-level JSON keys, and updates `sqs_consumer.py`'s call sites to pass structured fields instead of string-interpolating them into the message.

**Files:**
- Modify: `services/worker/src/campaign_worker/logging.py`
- Modify: `services/worker/src/campaign_worker/consumer/sqs_consumer.py`
- Test: `services/worker/tests/test_foundation.py` (add cases; file already exists per the codebase's logging smoke tests)

**Interfaces:**
- Produces: `JsonFormatter` now reads any `LogRecord` attribute not in Python's standard `logging.LogRecord` attribute set and includes it as a top-level JSON key (in addition to `timestamp`, `level`, `service`, `event`).

- [ ] **Step 1: Write the failing test**

```python
# services/worker/tests/test_foundation.py (append)
import json
import logging

from campaign_worker.logging import JsonFormatter


def test_json_formatter_includes_extra_structured_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="campaign_worker.consumer",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="lease_conflict",
        args=(),
        exc_info=None,
    )
    record.campaign_id = "018f0000-0000-7000-8000-000000000001"
    record.job_id = "018f0000-0000-7000-8000-000000000002"
    payload = json.loads(formatter.format(record))
    assert payload["event"] == "lease_conflict"
    assert payload["campaign_id"] == "018f0000-0000-7000-8000-000000000001"
    assert payload["job_id"] == "018f0000-0000-7000-8000-000000000002"


def test_json_formatter_omits_reserved_attributes():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="campaign_worker.consumer",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ok",
        args=(),
        exc_info=None,
    )
    payload = json.loads(formatter.format(record))
    assert set(payload) == {"timestamp", "level", "service", "event"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_foundation.py -v -k formatter`
Expected: FAIL — `AssertionError` (`campaign_id`/`job_id` keys missing from `payload`).

- [ ] **Step 3: Write minimal implementation**

```python
# services/worker/src/campaign_worker/logging.py
import json
import logging
from datetime import UTC, datetime

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": "campaign-worker",
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_foundation.py -v -k formatter`
Expected: PASS (2 tests)

- [ ] **Step 5: Update `sqs_consumer.py` call sites to pass structured fields**

In `services/worker/src/campaign_worker/consumer/sqs_consumer.py`, replace each `%s`-interpolated warning/info call with `extra=`. Apply this change to all five occurrences:

```python
            _LOG.warning("delivery_retry_exhausted", extra={"campaign_id": str(message.campaign_id), "job_id": str(message.job_id)})
```
(replaces `_LOG.warning("delivery_retry_exhausted campaign=%s job=%s", message.campaign_id, message.job_id)`)

```python
            _LOG.warning("campaign_version_unavailable", extra={"campaign_id": str(message.campaign_id), "job_id": str(message.job_id)})
```
(replaces the `campaign_version_unavailable` line)

```python
            _LOG.info("lease_conflict", extra={"campaign_id": str(message.campaign_id), "job_id": str(message.job_id)})
```
(replaces the `lease_conflict` line)

```python
                _LOG.warning("lease_lost", extra={"campaign_id": str(received.job.campaign_id), "job_id": str(received.job.job_id)})
```
(replaces the `lease_lost` line inside `_heartbeat_loop`)

```python
                _LOG.warning(
                    "visibility_heartbeat_failed",
                    extra={"campaign_id": str(received.job.campaign_id), "job_id": str(received.job.job_id)},
                )
```
(replaces the `visibility_heartbeat_failed` line)

- [ ] **Step 6: Run the full worker suite to confirm no regression**

Run: `cd services/worker && uv run pytest -q`
Expected: all existing tests still PASS — the existing assertion `test_invalid_message_remains_for_redrive_and_body_not_logged` must still hold (message bodies are still never logged; only IDs are now structured).

- [ ] **Step 7: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 8: Commit**

```bash
git add services/worker/src/campaign_worker/logging.py services/worker/src/campaign_worker/consumer/sqs_consumer.py services/worker/tests/test_foundation.py
git commit -m "fix(worker): emit correlation/campaign/job IDs as structured JSON log fields"
```

---

### Task 3: Remove dead lease methods

**Explanation:** `services/api/src/campaign_api/repositories/dynamodb_campaign_repository.py`'s `acquire_processing_lease`/`heartbeat_processing_lease` duplicate lease-acquisition logic that `services/worker`'s `DynamoDBWorkflowRepository` now independently owns (confirmed: no application code calls these two API-side methods — only their own tests do). Removing them eliminates a divergence risk between two independently-maintained implementations of the same concurrency-critical logic.

**Files:**
- Modify: `services/api/src/campaign_api/repositories/dynamodb_campaign_repository.py`
- Modify: `services/api/tests/test_dynamodb_repository.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task only removes code. `CampaignRepository` (the abstract base) never declared these methods, so no interface contract changes.

- [ ] **Step 1: Confirm no other call sites exist (safety check before deleting)**

Run: `cd services/api && grep -rn "acquire_processing_lease\|heartbeat_processing_lease" src tests`
Expected output: matches only inside `src/campaign_api/repositories/dynamodb_campaign_repository.py` (the definitions) and `tests/test_dynamodb_repository.py` (the test being removed in this task). If any other file matches, stop and re-scope this task — do not delete code with live callers.

- [ ] **Step 2: Remove the test that exercises the dead methods**

In `services/api/tests/test_dynamodb_repository.py`, delete the entire `test_processing_lease_conflict_and_heartbeat` function (the test starting at `@pytest.mark.asyncio` immediately above `async def test_processing_lease_conflict_and_heartbeat(repository, dynamodb):` and ending immediately before the blank lines preceding `@pytest.mark.asyncio` / `async def test_health_and_cleanup_guard(repository, dynamodb):`).

- [ ] **Step 3: Run the suite to verify it still passes without that test**

Run: `cd services/api && uv run pytest tests/test_dynamodb_repository.py -v`
Expected: PASS, one fewer test than before (`test_processing_lease_conflict_and_heartbeat` no longer present).

- [ ] **Step 4: Delete the dead methods from the repository class**

In `services/api/src/campaign_api/repositories/dynamodb_campaign_repository.py`, delete the two full method definitions `acquire_processing_lease` and `heartbeat_processing_lease` (from `async def acquire_processing_lease(` through the end of `heartbeat_processing_lease`'s body, immediately before `async def available(self) -> bool:`). Also remove the now-unused `datetime` import if nothing else in the file uses it — check first:

Run: `cd services/api && grep -n "datetime" src/campaign_api/repositories/dynamodb_campaign_repository.py`

If the only remaining reference to `datetime` after deletion is the `from datetime import datetime` import line itself, remove that import line too.

- [ ] **Step 5: Run the full suite to verify everything still passes**

Run: `cd services/api && uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 6: Format, lint, type-check, coverage**

Run: `cd services/api && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_api --cov-report=term-missing`
Expected: ruff clean (confirms no unused imports left behind), mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 7: Commit**

```bash
git add services/api/src/campaign_api/repositories/dynamodb_campaign_repository.py services/api/tests/test_dynamodb_repository.py
git commit -m "refactor(api): remove dead lease methods superseded by the worker's own repository"
```

---

### Task 4: Promote STEP into a typed Pydantic model

**Explanation:** Every persisted entity type except `STEP` already has a first-class Pydantic model (`CampaignAggregateMetadata`, `CampaignVersion`, `CampaignEvent`, `ApprovalRecord`); `dynamodb.py`'s `serialize_step` currently takes a raw `dict[str, Any]` payload. This task adds `WorkflowStepRecord` to the shared contracts package and changes `serialize_step` to take a typed model, matching every other `serialize_*` helper. Confirmed via repo-wide search: `serialize_step` has no application call sites today (only its own definition and two contract tests reference it), so changing its signature is safe.

**Files:**
- Create: `shared/src/campaign_contracts/steps.py`
- Modify: `shared/src/campaign_contracts/dynamodb.py`
- Modify: `shared/src/campaign_contracts/__init__.py`
- Modify: `shared/tests/test_persistence_and_schema.py`
- Test: `shared/tests/test_steps.py` (new)

**Interfaces:**
- Produces: `WorkflowStepRecord` (Pydantic model) in `campaign_contracts.steps`, importable as `from campaign_contracts.steps import WorkflowStepRecord`. Fields: `campaign_id: UUID`, `campaign_version: int (ge=1)`, `step: WorkflowStep`, `status: StepStatus`, `attempt: int (ge=0, default=0)`, `idempotency_key: str | None = None`, `output_checksum: str | None` (pattern `^[0-9a-f]{64}$`), `started_at: datetime | None = None`, `completed_at: datetime | None = None`, `created_at: datetime`, `updated_at: datetime`, `lock_version: int (ge=0, default=0)`.
- Produces: `serialize_step(step: WorkflowStepRecord) -> dict[str, Any]` in `campaign_contracts.dynamodb` (replaces the old 4-positional-argument signature).

- [ ] **Step 1: Write the failing test**

```python
# shared/tests/test_steps.py
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from campaign_contracts.enums import StepStatus, WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord


def _record(**overrides):
    now = datetime(2026, 8, 3, tzinfo=UTC)
    defaults = dict(
        campaign_id=UUID("018f0000-0000-7000-8000-000000000001"),
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.SUCCEEDED,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return WorkflowStepRecord(**defaults)


def test_workflow_step_record_accepts_valid_fields():
    record = _record(attempt=1, idempotency_key="k1")
    assert record.status == StepStatus.SUCCEEDED
    assert record.attempt == 1


def test_workflow_step_record_rejects_negative_attempt():
    with pytest.raises(ValidationError):
        _record(attempt=-1)


def test_workflow_step_record_rejects_bad_checksum_pattern():
    with pytest.raises(ValidationError):
        _record(output_checksum="not-a-checksum")


def test_workflow_step_record_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _record(unexpected_field="nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shared && uv run pytest tests/test_steps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_contracts.steps'`.

- [ ] **Step 3: Write minimal implementation**

```python
# shared/src/campaign_contracts/steps.py
from datetime import datetime
from uuid import UUID

from pydantic import Field

from .enums import StepStatus, WorkflowStep
from .validation import UTCModel

SHA256 = r"^[0-9a-f]{64}$"


class WorkflowStepRecord(UTCModel):
    campaign_id: UUID
    campaign_version: int = Field(ge=1)
    step: WorkflowStep
    status: StepStatus
    attempt: int = Field(default=0, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=128)
    output_checksum: str | None = Field(default=None, pattern=SHA256)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    lock_version: int = Field(default=0, ge=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shared && uv run pytest tests/test_steps.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Export `steps` from the package `__init__.py`**

```python
# shared/src/campaign_contracts/__init__.py
from .api import *
from .artifacts import *
from .campaign import *
from .dynamodb import *
from .enums import *
from .errors import *
from .events import *
from .sqs import *
from .steps import *
from .validation import *
```

- [ ] **Step 6: Change `serialize_step`'s signature to accept the typed model**

In `shared/src/campaign_contracts/dynamodb.py`, add the import and replace the function:

```python
from .steps import WorkflowStepRecord
```

(add alongside the existing `from .campaign import (...)` and `from .events import CampaignEvent` imports)

```python
def serialize_step(step: WorkflowStepRecord) -> dict[str, Any]:
    return serialize(step, "WORKFLOW_STEP", step_sk(step.campaign_version, step.step))
```

(replaces the old `def serialize_step(campaign_id, version, step, payload): ...` definition)

- [ ] **Step 7: Update the existing contract test that calls the old signature**

In `shared/tests/test_persistence_and_schema.py`, replace this line:

```python
    now=datetime(2026,7,28,tzinfo=timezone.utc); meta=CampaignAggregateMetadata(campaign_id=cid,current_version=1,title='x',created_at=now,updated_at=now,lock_version=1); assert serialize_meta(meta)['SK']=='META'; assert serialize_step(cid,1,WorkflowStep.COPY,{'attempt':1})['attempt']==Decimal(1)
```

with:

```python
    now=datetime(2026,7,28,tzinfo=timezone.utc); meta=CampaignAggregateMetadata(campaign_id=cid,current_version=1,title='x',created_at=now,updated_at=now,lock_version=1); assert serialize_meta(meta)['SK']=='META'
    from campaign_contracts.enums import StepStatus
    from campaign_contracts.steps import WorkflowStepRecord
    step_record=WorkflowStepRecord(campaign_id=cid,campaign_version=1,step=WorkflowStep.COPY,status=StepStatus.SUCCEEDED,attempt=1,created_at=now,updated_at=now)
    assert serialize_step(step_record)['attempt']==Decimal(1) and serialize_step(step_record)['SK']=='STEP#1#copy'
```

- [ ] **Step 8: Run the full shared contracts suite**

Run: `cd shared && uv run pytest -q`
Expected: all tests PASS, including `test_keys_and_serialization` and the new `test_steps.py`.

- [ ] **Step 9: Format, lint, type-check, coverage**

Run: `cd shared && uv run ruff format . 2>/dev/null; uv run mypy src 2>/dev/null; uv run pytest -q --cov=campaign_contracts --cov-report=term-missing`

(This package's `pyproject.toml` does not currently define `[tool.ruff]`/`[tool.mypy]` sections the way the services do — run whatever lint/type commands are configured at the repo root if present; otherwise confirm `pytest --cov` alone meets the `fail_under = 90` gate already defined in `shared/pyproject.toml`.)
Expected: coverage >= 90%.

- [ ] **Step 10: Commit**

```bash
git add shared/src/campaign_contracts/steps.py shared/src/campaign_contracts/dynamodb.py shared/src/campaign_contracts/__init__.py shared/tests/test_steps.py shared/tests/test_persistence_and_schema.py
git commit -m "feat(contracts): promote STEP into a typed WorkflowStepRecord model"
```

---

### Task 5: Build the LangGraph stateless executor

**Explanation:** Per the approved architecture, LangGraph must run as a stateless per-invocation node graph with no native checkpointer — DynamoDB (via the worker's repository) is the sole durability source. This task adds the `langgraph` dependency and builds the executor scaffolding (`GraphState`, `build_graph()`, `GraphExecutor`) proven against a single synthetic node; Task 6 will replace the synthetic node with the six real nodes.

**Files:**
- Modify: `services/worker/pyproject.toml`
- Create: `services/worker/src/campaign_worker/graph/__init__.py`
- Create: `services/worker/src/campaign_worker/graph/state.py`
- Create: `services/worker/src/campaign_worker/graph/executor.py`
- Test: `services/worker/tests/test_graph_executor.py` (new)

**Interfaces:**
- Produces: `GraphState` (`TypedDict` with a `version: CampaignVersion` key) in `campaign_worker.graph.state`.
- Produces: `GraphExecutor` in `campaign_worker.graph.executor` — `GraphExecutor(compiled_graph).run(version: CampaignVersion) -> CampaignVersion` (async).
- Produces: `build_graph() -> CompiledStateGraph` in `campaign_worker.graph.executor` (Task 6 will change this function's body to wire in the six real nodes; its signature stays stable).

- [ ] **Step 1: Add the `langgraph` dependency**

In `services/worker/pyproject.toml`, change:

```toml
dependencies = ["campaign-contracts==0.1.0", "boto3>=1.40,<2", "pydantic>=2.12,<3"]
```

to:

```toml
dependencies = ["campaign-contracts==0.1.0", "boto3>=1.40,<2", "pydantic>=2.12,<3", "langgraph>=0.2,<1"]
```

Run: `cd services/worker && uv sync`
Expected: `langgraph` installs successfully. If the installed API differs from what Step 3 below assumes (e.g., import path changes), this will surface immediately as an import error in Step 2 — treat that as expected TDD failure information, not a blocker; adjust the import to match the installed version before proceeding.

- [ ] **Step 2: Write the failing test**

```python
# services/worker/tests/test_graph_executor.py
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus

from campaign_worker.graph.executor import GraphExecutor, build_graph
from campaign_worker.graph.state import GraphState


def _brief():
    from campaign_contracts.api import CampaignCreationRequest

    return CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew",
        business_description="A local roaster offering weekly delivery.",
        campaign_goal="increase sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )


def _version():
    now = datetime.now(UTC)
    return CampaignVersion(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=_brief(),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )


@pytest.mark.asyncio
async def test_executor_runs_graph_and_returns_updated_version():
    async def passthrough(state: GraphState) -> GraphState:
        return state

    graph = build_graph(nodes={"passthrough": passthrough}, edges=[("passthrough",)])
    executor = GraphExecutor(graph)
    version = _version()
    result = await executor.run(version)
    assert result.campaign_id == version.campaign_id


@pytest.mark.asyncio
async def test_executor_uses_no_checkpointer():
    graph = build_graph(nodes={"noop": lambda state: state}, edges=[("noop",)])
    assert graph.checkpointer is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_graph_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_worker.graph'`.

- [ ] **Step 4: Write minimal implementation**

```python
# services/worker/src/campaign_worker/graph/__init__.py
"""Stateless LangGraph executor, nodes, and node-boundary wrappers."""
```

```python
# services/worker/src/campaign_worker/graph/state.py
from typing import TypedDict

from campaign_contracts.campaign import CampaignVersion


class GraphState(TypedDict):
    version: CampaignVersion
```

```python
# services/worker/src/campaign_worker/graph/executor.py
from collections.abc import Awaitable, Callable

from langgraph.graph import END, START, StateGraph

from campaign_contracts.campaign import CampaignVersion

from .state import GraphState

NodeFn = Callable[[GraphState], Awaitable[GraphState] | GraphState]


def build_graph(nodes: dict[str, NodeFn], edges: list[tuple[str, ...]]):
    graph = StateGraph(GraphState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)
    previous = START
    for (name,) in edges:
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


class GraphExecutor:
    def __init__(self, compiled_graph) -> None:
        self._graph = compiled_graph

    async def run(self, version: CampaignVersion) -> CampaignVersion:
        result = await self._graph.ainvoke({"version": version})
        return result["version"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_graph_executor.py -v`
Expected: PASS (2 tests). If `graph.checkpointer` is not a public attribute on the installed `langgraph` version, adjust the second test to assert the equivalent (e.g., that `build_graph` never passes a `checkpointer=` argument to `.compile()`) — the intent (no persistence configured) must still be verified.

- [ ] **Step 6: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean (add `# type: ignore[import-untyped]` on the `langgraph` import if it ships no type stubs, matching this codebase's existing pattern for `boto3`/`botocore`), all tests pass, coverage >= 90% (coverage may temporarily dip below 90% until Task 6 adds real node tests — if so, note it honestly in this task's completion report rather than suppressing the check).

- [ ] **Step 7: Commit**

```bash
git add services/worker/pyproject.toml services/worker/src/campaign_worker/graph/__init__.py services/worker/src/campaign_worker/graph/state.py services/worker/src/campaign_worker/graph/executor.py services/worker/tests/test_graph_executor.py
git commit -m "feat(worker): add stateless LangGraph executor scaffold with no checkpointer"
```

---

### Task 6: Implement the six text nodes

**Explanation:** Implements `receive_request`, `validate_input`, `analyze_campaign`, `create_strategy`, `generate_copy`, `create_storyboard` as deterministic functions over `CampaignVersion` — no Bedrock call. Only `create_strategy`, `generate_copy`, and `create_storyboard` produce content mapped to an existing `WorkflowStep` enum value (`STRATEGY`/`COPY`/`STORYBOARD`); the other three are preparatory/validation nodes with no dedicated step-tracking entity (consistent with the Regeneration Matrix, which only operates at STRATEGY/COPY/STORYBOARD/IMAGES/VIDEO granularity). `build_graph()`'s call site is updated to chain all six nodes in sequence, still without step-tracking or cancellation wrapping (added in Tasks 7 and 8).

**Files:**
- Create: `services/worker/src/campaign_worker/graph/nodes.py`
- Modify: `services/worker/src/campaign_worker/graph/executor.py` (wire the six real nodes)
- Test: `services/worker/tests/test_graph_nodes.py` (new)

**Interfaces:**
- Consumes: `GraphState` (Task 5), `CampaignVersion`/`StrategyOutput`/`CampaignCopy`/`ChannelCopy`/`Storyboard`/`StoryboardScene` (`shared/src/campaign_contracts/campaign.py`).
- Produces: six async functions in `campaign_worker.graph.nodes`: `receive_request`, `validate_input`, `analyze_campaign`, `create_strategy`, `generate_copy`, `create_storyboard`, each `Callable[[GraphState], Awaitable[GraphState]]`.

- [ ] **Step 1: Write the failing test**

```python
# services/worker/tests/test_graph_nodes.py
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus

from campaign_worker.graph import nodes
from campaign_worker.graph.state import GraphState


def _version(**overrides):
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram", "facebook"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals aged 25-40",
        call_to_action="Subscribe today",
    )
    defaults = dict(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


@pytest.mark.asyncio
async def test_validate_input_rejects_blank_description():
    brief = _version().brief.model_copy(update={"business_description": "                    "})
    state: GraphState = {"version": _version(brief=brief)}
    with pytest.raises(ValueError, match="business_description"):
        await nodes.validate_input(state)


@pytest.mark.asyncio
async def test_create_strategy_produces_schema_valid_output():
    state: GraphState = {"version": _version()}
    result = await nodes.create_strategy(state)
    strategy = result["version"].strategy
    assert strategy is not None
    assert strategy.audience == "Urban professionals aged 25-40"
    assert "instagram" in strategy.channel_rationale


@pytest.mark.asyncio
async def test_generate_copy_requires_prior_strategy():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    result = await nodes.generate_copy(strategized)
    copy = result["version"].campaign_copy
    assert copy is not None
    assert copy.call_to_action == "Subscribe today"
    assert len(copy.channel_variants) == 2


@pytest.mark.asyncio
async def test_create_storyboard_produces_three_scenes_totaling_valid_duration():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    copied = await nodes.generate_copy(strategized)
    result = await nodes.create_storyboard(copied)
    storyboard = result["version"].storyboard
    assert storyboard is not None
    assert [s.scene_number for s in storyboard.scenes] == [1, 2, 3]
    assert storyboard.total_duration_seconds == sum(s.duration_seconds for s in storyboard.scenes)


@pytest.mark.asyncio
async def test_receive_request_and_analyze_campaign_are_passthrough_safe():
    state: GraphState = {"version": _version()}
    after_receive = await nodes.receive_request(state)
    after_analyze = await nodes.analyze_campaign(after_receive)
    assert after_analyze["version"].campaign_id == state["version"].campaign_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_graph_nodes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_worker.graph.nodes'`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/worker/src/campaign_worker/graph/nodes.py
from campaign_contracts.campaign import CampaignCopy, ChannelCopy, Storyboard, StoryboardScene, StrategyOutput

from .state import GraphState


async def receive_request(state: GraphState) -> GraphState:
    return state


async def validate_input(state: GraphState) -> GraphState:
    if not state["version"].brief.business_description.strip():
        raise ValueError("business_description must not be blank")
    return state


async def analyze_campaign(state: GraphState) -> GraphState:
    return state


async def create_strategy(state: GraphState) -> GraphState:
    version = state["version"]
    brief = version.brief
    audience = brief.target_audience or "general audience"
    strategy = StrategyOutput(
        audience=audience,
        positioning=f"{brief.business_name} for {brief.product_or_service}",
        objective=brief.campaign_goal,
        key_message=brief.key_message or brief.campaign_goal,
        channel_rationale={platform: f"Reach {audience} on {platform}" for platform in brief.platforms},
    )
    return {"version": version.model_copy(update={"strategy": strategy})}


async def generate_copy(state: GraphState) -> GraphState:
    version = state["version"]
    brief = version.brief
    strategy = version.strategy
    if strategy is None:
        raise ValueError("generate_copy requires create_strategy to have run first")
    headline = f"{brief.business_name}: {strategy.key_message}"[:120]
    caption = brief.business_description[:200]
    cta = brief.call_to_action or "Learn more"
    hashtags = [f"#{brief.business_name.replace(' ', '')}"]
    copy = CampaignCopy(
        headline=headline,
        caption=caption,
        call_to_action=cta,
        hashtags=hashtags,
        channel_variants=[
            ChannelCopy(channel=platform, headline=headline, caption=caption, call_to_action=cta, hashtags=hashtags)
            for platform in brief.platforms
        ],
    )
    return {"version": version.model_copy(update={"campaign_copy": copy})}


async def create_storyboard(state: GraphState) -> GraphState:
    version = state["version"]
    strategy = version.strategy
    if strategy is None:
        raise ValueError("create_storyboard requires create_strategy to have run first")
    scenes = [
        StoryboardScene(
            scene_number=index,
            purpose=f"Scene {index} for {strategy.objective}",
            duration_seconds=5,
            narration=strategy.key_message,
            visual_prompt=f"{version.brief.product_or_service}, scene {index}",
            transition="cut",
        )
        for index in (1, 2, 3)
    ]
    storyboard = Storyboard(scenes=scenes, total_duration_seconds=15)
    return {"version": version.model_copy(update={"storyboard": storyboard})}
```

Update `services/worker/src/campaign_worker/graph/executor.py`'s node wiring by adding a new function that the job processor (Task 5's `GraphExecutor` stays generic) will call — add this to the bottom of `executor.py`:

```python
from . import nodes as _nodes


def build_default_graph():
    return build_graph(
        nodes={
            "receive_request": _nodes.receive_request,
            "validate_input": _nodes.validate_input,
            "analyze_campaign": _nodes.analyze_campaign,
            "create_strategy": _nodes.create_strategy,
            "generate_copy": _nodes.generate_copy,
            "create_storyboard": _nodes.create_storyboard,
        },
        edges=[
            ("receive_request",),
            ("validate_input",),
            ("analyze_campaign",),
            ("create_strategy",),
            ("generate_copy",),
            ("create_storyboard",),
        ],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_graph_nodes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Add an integration test proving the default graph runs all six nodes in order**

```python
# services/worker/tests/test_graph_executor.py (append)
from campaign_worker.graph.executor import build_default_graph


@pytest.mark.asyncio
async def test_default_graph_runs_all_six_nodes_end_to_end():
    graph = build_default_graph()
    executor = GraphExecutor(graph)
    result = await executor.run(_version())
    assert result.strategy is not None
    assert result.campaign_copy is not None
    assert result.storyboard is not None
```

Run: `cd services/worker && uv run pytest tests/test_graph_executor.py -v`
Expected: PASS (3 tests total in this file).

- [ ] **Step 6: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 7: Commit**

```bash
git add services/worker/src/campaign_worker/graph/nodes.py services/worker/src/campaign_worker/graph/executor.py services/worker/tests/test_graph_nodes.py services/worker/tests/test_graph_executor.py
git commit -m "feat(worker): implement the six deterministic text nodes and wire the default graph"
```

---

### Task 7: Implement STEP skip/reuse

**Explanation:** Targeted regeneration (an MVP requirement) needs each content-producing node to check whether its step is already `SUCCEEDED`/`REUSED` for the current version before re-running. This task extends `WorkflowRepository` with `get_step`/`save_step`, implements them in `DynamoDBWorkflowRepository` using Task 4's `WorkflowStepRecord`, adds an in-memory fake for tests, and adds a `with_step_tracking` decorator applied to `create_strategy`, `generate_copy`, and `create_storyboard` only (the three nodes with a matching `WorkflowStep` enum value).

**Files:**
- Modify: `services/worker/src/campaign_worker/repositories/workflow_repository.py`
- Modify: `services/worker/src/campaign_worker/repositories/dynamodb_workflow_repository.py`
- Modify: `services/worker/tests/test_consumer.py` (extend `FakeRepository` with `get_step`/`save_step`)
- Create: `services/worker/src/campaign_worker/graph/boundary.py`
- Modify: `services/worker/src/campaign_worker/graph/executor.py` (apply the wrapper in `build_default_graph`)
- Test: `services/worker/tests/test_graph_boundary.py` (new)
- Test: `services/worker/tests/test_dynamodb_workflow_repository.py` (extend)

**Interfaces:**
- Consumes: `WorkflowStepRecord` (Task 4).
- Produces: `WorkflowRepository.get_step(campaign_id: UUID, campaign_version: int, step: WorkflowStep) -> WorkflowStepRecord | None` and `WorkflowRepository.save_step(record: WorkflowStepRecord) -> None` (new abstract methods).
- Produces: `with_step_tracking(step: WorkflowStep, repository: WorkflowRepository) -> Callable[[NodeFn], NodeFn]` in `campaign_worker.graph.boundary`.

- [ ] **Step 1: Write the failing test for the wrapper**

```python
# services/worker/tests/test_graph_boundary.py
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.enums import StepStatus, WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.graph.boundary import with_step_tracking
from campaign_worker.graph.state import GraphState
from campaign_worker.repositories.workflow_repository import WorkflowRepository


class FakeStepRepository(WorkflowRepository):
    def __init__(self):
        self.steps: dict[tuple, WorkflowStepRecord] = {}
        self.save_calls = 0

    async def get_step(self, campaign_id, campaign_version, step):
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record):
        self.save_calls += 1
        self.steps[(record.campaign_id, record.campaign_version, record.step)] = record

    async def load_version(self, message):
        raise NotImplementedError

    async def acquire_lease(self, message, owner, now, expires_at):
        raise NotImplementedError

    async def heartbeat(self, message, lease, now, expires_at):
        raise NotImplementedError

    async def is_completed(self, message):
        raise NotImplementedError

    async def complete(self, message, lease, completed_at):
        raise NotImplementedError

    async def release(self, message, lease):
        raise NotImplementedError

    async def record_exhausted(self, message, receive_count, now):
        raise NotImplementedError

    async def record_invalid(self, campaign_id, code, message_id, now):
        raise NotImplementedError

    async def available(self):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_with_step_tracking_runs_node_and_records_success():
    repository = FakeStepRepository()
    calls = []

    async def node(state: GraphState) -> GraphState:
        calls.append(1)
        return state

    from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
    from campaign_contracts.api import CampaignCreationRequest
    from campaign_contracts.enums import CampaignStatus

    now = datetime.now(UTC)
    version = CampaignVersion(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=CampaignCreationRequest(
            business_name="Example Coffee",
            product_or_service="Cold brew",
            business_description="A local roaster offering weekly delivery.",
            campaign_goal="increase sales",
            platforms=["instagram"],
            tone="bright",
            language="en-US",
        ),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)
    await wrapped({"version": version})
    assert calls == [1]
    saved = repository.steps[(version.campaign_id, version.campaign_version, WorkflowStep.STRATEGY)]
    assert saved.status == StepStatus.SUCCEEDED
    assert repository.save_calls == 2  # RUNNING then SUCCEEDED


@pytest.mark.asyncio
async def test_with_step_tracking_skips_already_succeeded_step():
    repository = FakeStepRepository()
    campaign_id = uuid4()
    now = datetime.now(UTC)
    repository.steps[(campaign_id, 1, WorkflowStep.STRATEGY)] = WorkflowStepRecord(
        campaign_id=campaign_id,
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.SUCCEEDED,
        created_at=now,
        updated_at=now,
    )
    calls = []

    async def node(state: GraphState) -> GraphState:
        calls.append(1)
        return state

    from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
    from campaign_contracts.api import CampaignCreationRequest
    from campaign_contracts.enums import CampaignStatus

    version = CampaignVersion(
        campaign_id=campaign_id,
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=CampaignCreationRequest(
            business_name="Example Coffee",
            product_or_service="Cold brew",
            business_description="A local roaster offering weekly delivery.",
            campaign_goal="increase sales",
            platforms=["instagram"],
            tone="bright",
            language="en-US",
        ),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    wrapped = with_step_tracking(WorkflowStep.STRATEGY, repository)(node)
    await wrapped({"version": version})
    assert calls == []
    assert repository.save_calls == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_graph_boundary.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_worker.graph.boundary'`, and `TypeError` on `FakeStepRepository` because `WorkflowRepository` doesn't yet declare `get_step`/`save_step` as abstract (this is fine — it will still fail on the missing `boundary` module first).

- [ ] **Step 3: Extend the `WorkflowRepository` abstract base**

In `services/worker/src/campaign_worker/repositories/workflow_repository.py`, add imports and two abstract methods:

```python
from campaign_contracts.enums import WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord
```

(add alongside the existing `from campaign_contracts.campaign import CampaignVersion` / `from campaign_contracts.sqs import SQSJobMessage` imports)

```python
    @abstractmethod
    async def get_step(
        self, campaign_id: UUID, campaign_version: int, step: WorkflowStep
    ) -> WorkflowStepRecord | None: ...

    @abstractmethod
    async def save_step(self, record: WorkflowStepRecord) -> None: ...
```

(add inside the `WorkflowRepository` class, alongside the other abstract methods)

- [ ] **Step 4: Implement the two methods on `DynamoDBWorkflowRepository`**

In `services/worker/src/campaign_worker/repositories/dynamodb_workflow_repository.py`, update the imports:

```python
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.dynamodb import meta_sk, pk, serialize_step, step_sk, version_sk
from campaign_contracts.enums import WorkflowStep
from campaign_contracts.sqs import SQSJobMessage, duplicate_delivery_key
from campaign_contracts.steps import WorkflowStepRecord
```

and add the two methods to the class (near `available`):

```python
    async def get_step(
        self, campaign_id: UUID, campaign_version: int, step: WorkflowStep
    ) -> WorkflowStepRecord | None:
        item = await self._get(pk(campaign_id), step_sk(campaign_version, step))
        if item is None:
            return None
        accepted = set(WorkflowStepRecord.model_fields)
        return WorkflowStepRecord.model_validate({key: value for key, value in item.items() if key in accepted})

    async def save_step(self, record: WorkflowStepRecord) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_item,
                TableName=self._table_name,
                Item=_marshal(serialize_step(record)),
            )
        except ClientError as exc:
            raise PersistenceUnavailable("step persistence unavailable") from exc
```

- [ ] **Step 5: Extend `FakeRepository` in `test_consumer.py` so the existing consumer test suite keeps passing**

In `services/worker/tests/test_consumer.py`, add to `FakeRepository`:

```python
    async def get_step(self, campaign_id, campaign_version, step):
        return None

    async def save_step(self, record):
        pass
```

(add inside the `FakeRepository` class defined in that file, alongside its other async methods)

- [ ] **Step 6: Add DynamoDB integration test coverage for the two new methods**

```python
# services/worker/tests/test_dynamodb_workflow_repository.py (append)
from campaign_contracts.enums import StepStatus, WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord


@pytest.mark.asyncio
async def test_save_and_get_step_round_trip(repository, dynamodb):
    now = datetime.now(UTC)
    campaign_id = uuid4()
    record = WorkflowStepRecord(
        campaign_id=campaign_id,
        campaign_version=1,
        step=WorkflowStep.STRATEGY,
        status=StepStatus.SUCCEEDED,
        attempt=1,
        created_at=now,
        updated_at=now,
    )
    await repository.save_step(record)
    fetched = await repository.get_step(campaign_id, 1, WorkflowStep.STRATEGY)
    assert fetched is not None
    assert fetched.status == StepStatus.SUCCEEDED
    assert fetched.attempt == 1


@pytest.mark.asyncio
async def test_get_step_returns_none_when_absent(repository, dynamodb):
    assert await repository.get_step(uuid4(), 1, WorkflowStep.COPY) is None
```

(Match this file's existing fixture names — `repository`/`dynamodb` — exactly as used by the other tests already in this file; do not rename them.)

- [ ] **Step 7: Write `graph/boundary.py`**

```python
# services/worker/src/campaign_worker/graph/boundary.py
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from campaign_contracts.enums import StepStatus, WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.repositories.workflow_repository import WorkflowRepository

from .state import GraphState

NodeFn = Callable[[GraphState], Awaitable[GraphState]]


def with_step_tracking(step: WorkflowStep, repository: WorkflowRepository) -> Callable[[NodeFn], NodeFn]:
    def decorator(fn: NodeFn) -> NodeFn:
        async def wrapped(state: GraphState) -> GraphState:
            version = state["version"]
            existing = await repository.get_step(version.campaign_id, version.campaign_version, step)
            if existing is not None and existing.status in (StepStatus.SUCCEEDED, StepStatus.REUSED):
                return state
            now = datetime.now(UTC)
            await repository.save_step(
                WorkflowStepRecord(
                    campaign_id=version.campaign_id,
                    campaign_version=version.campaign_version,
                    step=step,
                    status=StepStatus.RUNNING,
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            result = await fn(state)
            completed_at = datetime.now(UTC)
            await repository.save_step(
                WorkflowStepRecord(
                    campaign_id=version.campaign_id,
                    campaign_version=version.campaign_version,
                    step=step,
                    status=StepStatus.SUCCEEDED,
                    started_at=now,
                    completed_at=completed_at,
                    created_at=now,
                    updated_at=completed_at,
                )
            )
            return result

        return wrapped

    return decorator
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_graph_boundary.py tests/test_dynamodb_workflow_repository.py tests/test_consumer.py -v`
Expected: PASS (all tests, including the 2 new boundary tests and 2 new DynamoDB tests).

- [ ] **Step 9: Wire `with_step_tracking` into `build_default_graph`**

In `services/worker/src/campaign_worker/graph/executor.py`, update `build_default_graph` to accept a `repository` and apply the wrapper to the three content-producing nodes:

```python
from campaign_contracts.enums import WorkflowStep

from .boundary import with_step_tracking
from campaign_worker.repositories.workflow_repository import WorkflowRepository


def build_default_graph(repository: WorkflowRepository):
    return build_graph(
        nodes={
            "receive_request": _nodes.receive_request,
            "validate_input": _nodes.validate_input,
            "analyze_campaign": _nodes.analyze_campaign,
            "create_strategy": with_step_tracking(WorkflowStep.STRATEGY, repository)(_nodes.create_strategy),
            "generate_copy": with_step_tracking(WorkflowStep.COPY, repository)(_nodes.generate_copy),
            "create_storyboard": with_step_tracking(WorkflowStep.STORYBOARD, repository)(_nodes.create_storyboard),
        },
        edges=[
            ("receive_request",),
            ("validate_input",),
            ("analyze_campaign",),
            ("create_strategy",),
            ("generate_copy",),
            ("create_storyboard",),
        ],
    )
```

(This changes `build_default_graph`'s signature from no-argument to requiring `repository` — update the Task 6 integration test `test_default_graph_runs_all_six_nodes_end_to_end` in `test_graph_executor.py` to pass a `FakeStepRepository`-style double: import or duplicate the minimal fake from `test_graph_boundary.py`, e.g. add `from tests... ` is not valid across files in this layout, so instead define a small local fake in `test_graph_executor.py` reusing the same shape as `FakeStepRepository`, and update the call to `build_default_graph(repository)`.)

- [ ] **Step 10: Run the full worker suite**

Run: `cd services/worker && uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 11: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 12: Commit**

```bash
git add services/worker/src/campaign_worker/repositories/workflow_repository.py services/worker/src/campaign_worker/repositories/dynamodb_workflow_repository.py services/worker/src/campaign_worker/graph/boundary.py services/worker/src/campaign_worker/graph/executor.py services/worker/tests/test_graph_boundary.py services/worker/tests/test_dynamodb_workflow_repository.py services/worker/tests/test_consumer.py services/worker/tests/test_graph_executor.py
git commit -m "feat(worker): implement STEP skip/reuse for targeted regeneration"
```

---

### Task 8: Implement the shared cancellation wrapper

**Explanation:** The lifecycle contract's cancellation-during-work protocol needs a consistent, single implementation rather than being hand-rolled per node. This task adds `with_cancellation_check`, applied to all six nodes (composed with `with_step_tracking` for the three that have it). Since the `/cancel` API endpoint does not exist yet (out of Week 1 scope), the cancellation signal source is an injected predicate function — real wiring to a persisted cancellation flag is future work, documented here as an explicit boundary.

**Files:**
- Modify: `services/worker/src/campaign_worker/graph/boundary.py`
- Modify: `services/worker/src/campaign_worker/graph/executor.py`
- Test: `services/worker/tests/test_graph_boundary.py` (extend)

**Interfaces:**
- Produces: `NodeCancelled` exception and `with_cancellation_check(is_cancelled: Callable[[], Awaitable[bool]], step: str) -> Callable[[NodeFn], NodeFn]` in `campaign_worker.graph.boundary`.

- [ ] **Step 1: Write the failing test**

```python
# services/worker/tests/test_graph_boundary.py (append)
from campaign_worker.graph.boundary import NodeCancelled, with_cancellation_check


@pytest.mark.asyncio
async def test_with_cancellation_check_raises_when_cancelled():
    async def node(state: GraphState) -> GraphState:
        return state

    async def is_cancelled() -> bool:
        return True

    wrapped = with_cancellation_check(is_cancelled, "create_strategy")(node)
    with pytest.raises(NodeCancelled, match="create_strategy"):
        await wrapped({"version": None})


@pytest.mark.asyncio
async def test_with_cancellation_check_runs_node_when_not_cancelled():
    calls = []

    async def node(state: GraphState) -> GraphState:
        calls.append(1)
        return state

    async def is_cancelled() -> bool:
        return False

    wrapped = with_cancellation_check(is_cancelled, "create_strategy")(node)
    await wrapped({"version": None})
    assert calls == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_graph_boundary.py -v -k cancellation`
Expected: FAIL — `ImportError: cannot import name 'NodeCancelled'`.

- [ ] **Step 3: Write minimal implementation**

Append to `services/worker/src/campaign_worker/graph/boundary.py`:

```python
class NodeCancelled(Exception):
    def __init__(self, step: str) -> None:
        super().__init__(f"node cancelled before running: {step}")
        self.step = step


def with_cancellation_check(
    is_cancelled: Callable[[], Awaitable[bool]], step: str
) -> Callable[[NodeFn], NodeFn]:
    def decorator(fn: NodeFn) -> NodeFn:
        async def wrapped(state: GraphState) -> GraphState:
            if await is_cancelled():
                raise NodeCancelled(step)
            return await fn(state)

        return wrapped

    return decorator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_graph_boundary.py -v`
Expected: PASS (all tests in the file, including the 2 new ones).

- [ ] **Step 5: Compose both wrappers in `build_default_graph`**

In `services/worker/src/campaign_worker/graph/executor.py`, update the import and function:

```python
from .boundary import with_cancellation_check, with_step_tracking
from collections.abc import Awaitable, Callable


def build_default_graph(repository: WorkflowRepository, is_cancelled: Callable[[], Awaitable[bool]]):
    def cancellable(name: str, fn):
        return with_cancellation_check(is_cancelled, name)(fn)

    return build_graph(
        nodes={
            "receive_request": cancellable("receive_request", _nodes.receive_request),
            "validate_input": cancellable("validate_input", _nodes.validate_input),
            "analyze_campaign": cancellable("analyze_campaign", _nodes.analyze_campaign),
            "create_strategy": cancellable(
                "create_strategy", with_step_tracking(WorkflowStep.STRATEGY, repository)(_nodes.create_strategy)
            ),
            "generate_copy": cancellable(
                "generate_copy", with_step_tracking(WorkflowStep.COPY, repository)(_nodes.generate_copy)
            ),
            "create_storyboard": cancellable(
                "create_storyboard",
                with_step_tracking(WorkflowStep.STORYBOARD, repository)(_nodes.create_storyboard),
            ),
        },
        edges=[
            ("receive_request",),
            ("validate_input",),
            ("analyze_campaign",),
            ("create_strategy",),
            ("generate_copy",),
            ("create_storyboard",),
        ],
    )
```

Update the Task 6/7 integration test in `test_graph_executor.py` (`test_default_graph_runs_all_six_nodes_end_to_end`) to pass a no-op `async def is_cancelled(): return False` alongside the fake repository: `build_default_graph(repository, is_cancelled)`.

Add one more integration-level test proving cancellation actually stops the pipeline:

```python
# services/worker/tests/test_graph_executor.py (append)
@pytest.mark.asyncio
async def test_default_graph_raises_node_cancelled_when_cancellation_flagged():
    from campaign_worker.graph.boundary import NodeCancelled

    class _Repo:
        async def get_step(self, *args):
            return None

        async def save_step(self, record):
            pass

    async def is_cancelled():
        return True

    graph = build_default_graph(_Repo(), is_cancelled)
    executor = GraphExecutor(graph)
    with pytest.raises(NodeCancelled):
        await executor.run(_version())
```

- [ ] **Step 6: Run the full worker suite**

Run: `cd services/worker && uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 7: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 8: Commit**

```bash
git add services/worker/src/campaign_worker/graph/boundary.py services/worker/src/campaign_worker/graph/executor.py services/worker/tests/test_graph_boundary.py services/worker/tests/test_graph_executor.py
git commit -m "feat(worker): add shared cancellation-check wrapper for graph nodes"
```

**Documented deviation:** `is_cancelled` is an injected predicate with no real implementation yet — there is no persisted `cancellation_requested_at` field on `CampaignVersion` and no `/cancel` endpoint in `services/api` (both explicitly out of Week 1 scope). Wiring a real cancellation signal is future work; this task delivers the mechanism, not the live signal source.

---

### Task 9: Scaffold Marketing MCP as a separate service

**Explanation:** Per the approved architecture, Marketing MCP is deployed as its own service. This task creates `services/marketing-mcp/` following the exact same package layout as `services/api` and `services/worker`, with a testable `MarketingMCPService` class implementing the 8 tools from `docs/contracts/api-contracts.md`/spec §12 against an in-memory store (mirroring `InMemoryCampaignRepository`'s idempotency pattern), plus a thin `FastMCP`-based tool-registration layer. The worker does not call this service over the network this week — that wiring is explicitly deferred.

**Files:**
- Create: `services/marketing-mcp/pyproject.toml`
- Create: `services/marketing-mcp/.env.example`
- Create: `services/marketing-mcp/src/campaign_marketing_mcp/__init__.py`
- Create: `services/marketing-mcp/src/campaign_marketing_mcp/service.py`
- Create: `services/marketing-mcp/src/campaign_marketing_mcp/server.py`
- Test: `services/marketing-mcp/tests/test_service.py`

**Interfaces:**
- Produces: `MarketingMCPService` in `campaign_marketing_mcp.service` with async methods `create_campaign(aggregate, version, idempotency_key) -> tuple[CampaignAggregateMetadata, CampaignVersion]`, `get_campaign(campaign_id) -> tuple[CampaignAggregateMetadata, CampaignVersion] | None`, `update_campaign_status(campaign_id, campaign_version, status, idempotency_key) -> CampaignVersion`, `save_campaign_content(campaign_id, campaign_version, field, payload, idempotency_key) -> CampaignVersion`, `save_asset_metadata(artifact) -> None`, `validate_campaign_package(campaign_id) -> bool`, `prepare_delivery_package(campaign_id, campaign_version, idempotency_key) -> str`, `update_campaign(campaign_id, patch, idempotency_key) -> CampaignVersion`.

- [ ] **Step 1: Create the package skeleton**

```toml
# services/marketing-mcp/pyproject.toml
[build-system]
requires = ["hatchling>=1.26"]
build-backend = "hatchling.build"

[project]
name = "campaign-marketing-mcp"
version = "0.1.0"
description = "Marketing MCP service scaffold: typed persistence, transitions, assets, and packaging tools"
requires-python = ">=3.12,<3.13"
dependencies = ["campaign-contracts==0.1.0", "mcp>=1.2,<2"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "pytest-cov>=5", "ruff>=0.12", "mypy>=1.17"]

[tool.hatch.build.targets.wheel]
packages = ["src/campaign_marketing_mcp"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py312"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["campaign_marketing_mcp"]
mypy_path = "src"

[tool.coverage.run]
source = ["campaign_marketing_mcp"]

[tool.coverage.report]
fail_under = 90
show_missing = true

[tool.uv.sources]
campaign-contracts = { path = "../../shared", editable = true }
```

```
# services/marketing-mcp/.env.example
SERVICE_NAME=campaign-marketing-mcp
ENVIRONMENT=local
LOG_LEVEL=INFO
```

```python
# services/marketing-mcp/src/campaign_marketing_mcp/__init__.py
"""Marketing MCP service scaffold."""
```

Run: `cd services/marketing-mcp && uv sync`
Expected: dependencies install successfully (verifies the `mcp` package name/version floor is installable; if not, adjust the version constraint and re-run before proceeding).

- [ ] **Step 2: Write the failing test**

```python
# services/marketing-mcp/tests/test_service.py
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus

from campaign_marketing_mcp.service import DuplicateCampaign, MarketingMCPService


def _brief():
    return CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew",
        business_description="A local roaster offering weekly delivery.",
        campaign_goal="increase sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )


def _records():
    now = datetime.now(UTC)
    campaign_id = uuid4()
    aggregate = CampaignAggregateMetadata(
        campaign_id=campaign_id, current_version=1, title="Example Coffee", created_at=now, updated_at=now, lock_version=0
    )
    version = CampaignVersion(
        campaign_id=campaign_id,
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.CREATED,
        progress_percent=0,
        brief=_brief(),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=0,
    )
    return aggregate, version


@pytest.mark.asyncio
async def test_create_campaign_is_idempotent_for_same_key():
    service = MarketingMCPService()
    aggregate, version = _records()
    first = await service.create_campaign(aggregate, version, idempotency_key="key-1")
    second = await service.create_campaign(aggregate, version, idempotency_key="key-1")
    assert first == second


@pytest.mark.asyncio
async def test_create_campaign_conflicts_on_different_key_same_id():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    with pytest.raises(DuplicateCampaign):
        await service.create_campaign(aggregate, version, idempotency_key="key-2")


@pytest.mark.asyncio
async def test_get_campaign_returns_none_when_absent():
    service = MarketingMCPService()
    assert await service.get_campaign(uuid4()) is None


@pytest.mark.asyncio
async def test_save_campaign_content_updates_strategy_field():
    from campaign_contracts.campaign import StrategyOutput

    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    strategy = StrategyOutput(
        audience="general audience", positioning="x", objective="y", key_message="z", channel_rationale={}
    )
    updated = await service.save_campaign_content(
        version.campaign_id, version.campaign_version, "strategy", strategy, idempotency_key="content-1"
    )
    assert updated.strategy == strategy


@pytest.mark.asyncio
async def test_update_campaign_status_transitions_and_records():
    service = MarketingMCPService()
    aggregate, version = _records()
    await service.create_campaign(aggregate, version, idempotency_key="key-1")
    updated = await service.update_campaign_status(
        version.campaign_id, version.campaign_version, CampaignStatus.QUEUED, idempotency_key="status-1"
    )
    assert updated.status == CampaignStatus.QUEUED
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/marketing-mcp && uv run pytest tests/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_marketing_mcp.service'`.

- [ ] **Step 4: Write minimal implementation**

```python
# services/marketing-mcp/src/campaign_marketing_mcp/service.py
from typing import Any
from uuid import UUID

from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignVersion
from campaign_contracts.enums import CampaignStatus


class DuplicateCampaign(Exception):
    pass


class CampaignNotFound(Exception):
    pass


class MarketingMCPService:
    """Scaffold: in-memory store standing in for the real DynamoDB/S3-backed implementation."""

    def __init__(self) -> None:
        self._campaigns: dict[UUID, tuple[CampaignAggregateMetadata, CampaignVersion]] = {}
        self._idempotency: dict[tuple[UUID, str], str] = {}
        self._packages: dict[tuple[UUID, int], str] = {}
        self._assets: list[Any] = []

    async def create_campaign(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, *, idempotency_key: str
    ) -> tuple[CampaignAggregateMetadata, CampaignVersion]:
        existing_key = self._idempotency.get((aggregate.campaign_id, "create_campaign"))
        if existing_key is not None:
            if existing_key != idempotency_key:
                raise DuplicateCampaign("campaign already exists with a different idempotency key")
            return self._campaigns[aggregate.campaign_id]
        self._idempotency[(aggregate.campaign_id, "create_campaign")] = idempotency_key
        self._campaigns[aggregate.campaign_id] = (aggregate, version)
        return aggregate, version

    async def get_campaign(
        self, campaign_id: UUID
    ) -> tuple[CampaignAggregateMetadata, CampaignVersion] | None:
        return self._campaigns.get(campaign_id)

    async def update_campaign(
        self, campaign_id: UUID, patch: dict[str, Any], *, idempotency_key: str
    ) -> CampaignVersion:
        record = self._campaigns.get(campaign_id)
        if record is None:
            raise CampaignNotFound(str(campaign_id))
        aggregate, version = record
        updated = version.model_copy(update=patch)
        self._campaigns[campaign_id] = (aggregate, updated)
        return updated

    async def update_campaign_status(
        self, campaign_id: UUID, campaign_version: int, status: CampaignStatus, *, idempotency_key: str
    ) -> CampaignVersion:
        record = self._campaigns.get(campaign_id)
        if record is None:
            raise CampaignNotFound(str(campaign_id))
        aggregate, version = record
        updated = version.model_copy(update={"status": status})
        self._campaigns[campaign_id] = (aggregate, updated)
        return updated

    async def save_campaign_content(
        self, campaign_id: UUID, campaign_version: int, field: str, payload: Any, *, idempotency_key: str
    ) -> CampaignVersion:
        record = self._campaigns.get(campaign_id)
        if record is None:
            raise CampaignNotFound(str(campaign_id))
        aggregate, version = record
        updated = version.model_copy(update={field: payload})
        self._campaigns[campaign_id] = (aggregate, updated)
        return updated

    async def save_asset_metadata(self, artifact: Any) -> None:
        self._assets.append(artifact)

    async def validate_campaign_package(self, campaign_id: UUID) -> bool:
        record = self._campaigns.get(campaign_id)
        if record is None:
            return False
        _, version = record
        return version.strategy is not None and version.campaign_copy is not None and version.storyboard is not None

    async def prepare_delivery_package(
        self, campaign_id: UUID, campaign_version: int, *, idempotency_key: str
    ) -> str:
        key = (campaign_id, campaign_version)
        if key in self._packages:
            return self._packages[key]
        package_id = f"package-{campaign_id}-{campaign_version}"
        self._packages[key] = package_id
        return package_id
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/marketing-mcp && uv run pytest tests/test_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Add the thin FastMCP tool-registration layer**

```python
# services/marketing-mcp/src/campaign_marketing_mcp/server.py
from mcp.server.fastmcp import FastMCP

from .service import MarketingMCPService

mcp = FastMCP("marketing-mcp")
_service = MarketingMCPService()


@mcp.tool()
async def get_campaign(campaign_id: str) -> dict | None:
    from uuid import UUID

    record = await _service.get_campaign(UUID(campaign_id))
    if record is None:
        return None
    aggregate, version = record
    return {"aggregate": aggregate.model_dump(mode="json"), "version": version.model_dump(mode="json", by_alias=True)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

**Documented deviation:** only `get_campaign` is wired through `FastMCP` this week as a smoke-level proof that the transport layer loads and registers a tool; the other 7 tools exist on `MarketingMCPService` (fully tested) but are not yet exposed through `server.py`'s MCP decorators, and the worker does not call this service over the network. Full tool exposure and worker integration are Week 2 scope.

- [ ] **Step 7: Add a smoke test confirming the server module imports and registers the tool**

```python
# services/marketing-mcp/tests/test_server.py
def test_server_module_imports_and_registers_get_campaign_tool():
    from campaign_marketing_mcp.server import mcp

    tool_names = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert "get_campaign" in tool_names
```

Run: `cd services/marketing-mcp && uv run pytest tests/test_server.py -v`
Expected: PASS. If `FastMCP`'s internal attribute name for listing registered tools differs from `_tool_manager.list_tools()` in the installed `mcp` version, adjust this assertion to whatever the installed SDK's public API exposes for tool introspection (check `mcp.server.fastmcp.FastMCP`'s public methods) — this is exactly the kind of API-surface risk this step's TDD cycle is designed to catch immediately.

- [ ] **Step 8: Format, lint, type-check, coverage**

Run: `cd services/marketing-mcp && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_marketing_mcp --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 9: Commit**

```bash
git add services/marketing-mcp/
git commit -m "feat(marketing-mcp): scaffold Marketing MCP as its own service package"
```

---

### Task 10: Create the provider abstraction layer

**Explanation:** Defines `ImageProvider`/`VideoProvider` interfaces so real and mock media providers are swappable without touching graph/node code — mirroring the `QUEUE_BACKEND=memory|sqs` factory pattern already used in `services/api`. Standalone this week; not yet wired into any graph node (no `generate_images`/`render_video` node exists yet — that's Week 2+ scope).

**Files:**
- Create: `services/worker/src/campaign_worker/providers/__init__.py`
- Create: `services/worker/src/campaign_worker/providers/base.py`
- Test: `services/worker/tests/test_providers_base.py`

**Interfaces:**
- Produces: `ImageProvider` (ABC, `generate_image(campaign_id: UUID, campaign_version: int, prompt: ImagePrompt) -> ImageArtifactReference`) and `VideoProvider` (ABC, `render_video(campaign_id: UUID, campaign_version: int, storyboard: Storyboard, image_artifacts: list[ImageArtifactReference]) -> VideoArtifactReference`) in `campaign_worker.providers.base`.

- [ ] **Step 1: Write the failing test**

```python
# services/worker/tests/test_providers_base.py
import inspect

from campaign_worker.providers.base import ImageProvider, VideoProvider


def test_image_provider_is_abstract_with_generate_image():
    assert inspect.isabstract(ImageProvider)
    assert "generate_image" in ImageProvider.__abstractmethods__


def test_video_provider_is_abstract_with_render_video():
    assert inspect.isabstract(VideoProvider)
    assert "render_video" in VideoProvider.__abstractmethods__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_providers_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_worker.providers'`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/worker/src/campaign_worker/providers/__init__.py
"""Provider abstraction layer: ImageProvider/VideoProvider and mock implementations."""
```

```python
# services/worker/src/campaign_worker/providers/base.py
from abc import ABC, abstractmethod
from uuid import UUID

from campaign_contracts.artifacts import ImageArtifactReference, VideoArtifactReference
from campaign_contracts.campaign import ImagePrompt, Storyboard


class ImageProvider(ABC):
    @abstractmethod
    async def generate_image(
        self, campaign_id: UUID, campaign_version: int, prompt: ImagePrompt
    ) -> ImageArtifactReference: ...


class VideoProvider(ABC):
    @abstractmethod
    async def render_video(
        self,
        campaign_id: UUID,
        campaign_version: int,
        storyboard: Storyboard,
        image_artifacts: list[ImageArtifactReference],
    ) -> VideoArtifactReference: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_providers_base.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 6: Commit**

```bash
git add services/worker/src/campaign_worker/providers/__init__.py services/worker/src/campaign_worker/providers/base.py services/worker/tests/test_providers_base.py
git commit -m "feat(worker): add ImageProvider/VideoProvider abstraction layer"
```

---

### Task 11: Implement deterministic mock Image Provider

**Explanation:** A clearly-synthetic mock implementation of `ImageProvider`, used for development and rehearsal — never shown to evaluators as real generated output. Deterministic: the same campaign/version/prompt always produces the same checksum, so tests are reproducible.

**Files:**
- Create: `services/worker/src/campaign_worker/providers/mock_image_provider.py`
- Test: `services/worker/tests/test_mock_image_provider.py`

**Interfaces:**
- Produces: `MockImageProvider` (implements `ImageProvider`) in `campaign_worker.providers.mock_image_provider`.

- [ ] **Step 1: Write the failing test**

```python
# services/worker/tests/test_mock_image_provider.py
from uuid import uuid4

import pytest
from campaign_contracts.campaign import ImagePrompt
from campaign_contracts.enums import ArtifactType

from campaign_worker.providers.mock_image_provider import MockImageProvider


@pytest.mark.asyncio
async def test_mock_image_provider_produces_valid_artifact():
    provider = MockImageProvider()
    campaign_id = uuid4()
    prompt = ImagePrompt(scene_number=1, prompt="a cup of cold brew coffee, scene 1")
    artifact = await provider.generate_image(campaign_id, 1, prompt)
    assert artifact.campaign_id == campaign_id
    assert artifact.campaign_version == 1
    assert artifact.artifact_type == ArtifactType.IMAGE
    assert artifact.provider == "mock-image-provider"
    assert len(artifact.checksum_sha256) == 64


@pytest.mark.asyncio
async def test_mock_image_provider_is_deterministic():
    provider = MockImageProvider()
    campaign_id = uuid4()
    prompt = ImagePrompt(scene_number=2, prompt="a cup of cold brew coffee, scene 2")
    first = await provider.generate_image(campaign_id, 1, prompt)
    second = await provider.generate_image(campaign_id, 1, prompt)
    assert first.checksum_sha256 == second.checksum_sha256
    assert first.artifact_id != second.artifact_id  # each call is still a distinct artifact record
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_mock_image_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_worker.providers.mock_image_provider'`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/worker/src/campaign_worker/providers/mock_image_provider.py
import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from campaign_contracts.artifacts import ImageArtifactReference
from campaign_contracts.campaign import ImagePrompt
from campaign_contracts.enums import WorkflowStep

from .base import ImageProvider


class MockImageProvider(ImageProvider):
    """Deterministic, clearly-synthetic mock. Never disclosed as a real generated asset."""

    async def generate_image(
        self, campaign_id: UUID, campaign_version: int, prompt: ImagePrompt
    ) -> ImageArtifactReference:
        digest = hashlib.sha256(f"{campaign_id}:{campaign_version}:{prompt.scene_number}:{prompt.prompt}".encode())
        return ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/png",
            size_bytes=1024,
            checksum_sha256=digest.hexdigest(),
            created_at=datetime.now(UTC),
            provider="mock-image-provider",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_mock_image_provider.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 6: Commit**

```bash
git add services/worker/src/campaign_worker/providers/mock_image_provider.py services/worker/tests/test_mock_image_provider.py
git commit -m "feat(worker): add deterministic MockImageProvider"
```

---

### Task 12: Implement deterministic mock Video Provider

**Explanation:** Same rationale as Task 11, for `VideoProvider`. Deterministic checksum derived from the storyboard content and the set of image artifact IDs used, so a given approved storyboard always mock-renders identically.

**Files:**
- Create: `services/worker/src/campaign_worker/providers/mock_video_provider.py`
- Test: `services/worker/tests/test_mock_video_provider.py`

**Interfaces:**
- Produces: `MockVideoProvider` (implements `VideoProvider`) in `campaign_worker.providers.mock_video_provider`.

- [ ] **Step 1: Write the failing test**

```python
# services/worker/tests/test_mock_video_provider.py
from uuid import uuid4

import pytest
from campaign_contracts.campaign import Storyboard, StoryboardScene
from campaign_contracts.enums import ArtifactType

from campaign_worker.providers.mock_video_provider import MockVideoProvider


def _storyboard():
    scenes = [
        StoryboardScene(
            scene_number=i,
            purpose=f"scene {i}",
            duration_seconds=5,
            narration="narration",
            visual_prompt=f"prompt {i}",
            transition="cut",
        )
        for i in (1, 2, 3)
    ]
    return Storyboard(scenes=scenes, total_duration_seconds=15)


@pytest.mark.asyncio
async def test_mock_video_provider_produces_valid_artifact():
    provider = MockVideoProvider()
    campaign_id = uuid4()
    artifact = await provider.render_video(campaign_id, 1, _storyboard(), [])
    assert artifact.campaign_id == campaign_id
    assert artifact.artifact_type == ArtifactType.VIDEO
    assert artifact.provider == "mock-video-provider"
    assert len(artifact.checksum_sha256) == 64


@pytest.mark.asyncio
async def test_mock_video_provider_is_deterministic_for_same_storyboard():
    provider = MockVideoProvider()
    campaign_id = uuid4()
    storyboard = _storyboard()
    first = await provider.render_video(campaign_id, 1, storyboard, [])
    second = await provider.render_video(campaign_id, 1, storyboard, [])
    assert first.checksum_sha256 == second.checksum_sha256
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/worker && uv run pytest tests/test_mock_video_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'campaign_worker.providers.mock_video_provider'`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/worker/src/campaign_worker/providers/mock_video_provider.py
import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from campaign_contracts.artifacts import ImageArtifactReference, VideoArtifactReference
from campaign_contracts.campaign import Storyboard
from campaign_contracts.enums import WorkflowStep

from .base import VideoProvider


class MockVideoProvider(VideoProvider):
    """Deterministic, clearly-synthetic mock. Never disclosed as a real generated asset."""

    async def render_video(
        self,
        campaign_id: UUID,
        campaign_version: int,
        storyboard: Storyboard,
        image_artifacts: list[ImageArtifactReference],
    ) -> VideoArtifactReference:
        scene_signature = "|".join(f"{s.scene_number}:{s.visual_prompt}" for s in storyboard.scenes)
        image_signature = "|".join(str(artifact.artifact_id) for artifact in image_artifacts)
        digest = hashlib.sha256(f"{campaign_id}:{campaign_version}:{scene_signature}:{image_signature}".encode())
        return VideoArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            workflow_step=WorkflowStep.VIDEO,
            mime_type="video/mp4",
            size_bytes=2_000_000,
            checksum_sha256=digest.hexdigest(),
            created_at=datetime.now(UTC),
            provider="mock-video-provider",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/worker && uv run pytest tests/test_mock_video_provider.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Format, lint, type-check, coverage**

Run: `cd services/worker && uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing`
Expected: ruff clean, mypy clean, all tests pass, coverage >= 90%.

- [ ] **Step 6: Commit**

```bash
git add services/worker/src/campaign_worker/providers/mock_video_provider.py services/worker/tests/test_mock_video_provider.py
git commit -m "feat(worker): add deterministic MockVideoProvider"
```

---

## End-of-Week-1 Verification

After all 12 tasks are complete, run the full suite for all three Python packages and confirm nothing regressed:

```bash
cd shared && uv run pytest -q --cov=campaign_contracts --cov-report=term-missing
cd ../services/api && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_api --cov-report=term-missing
cd ../services/worker && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_worker --cov-report=term-missing
cd ../marketing-mcp && uv run ruff check . && uv run mypy . && uv run pytest -q --cov=campaign_marketing_mcp --cov-report=term-missing
```

Expected: all four packages pass lint/type-check, all tests pass, all coverage gates (>= 90%) met.

**Explicit Week 1 boundary (do not exceed):** the graph does not call Bedrock, Image Generator MCP, or HyperFrames MCP. `GraphExecutor` is not yet wired into `services/worker/src/campaign_worker/main.py`'s `build_consumer()` in place of `NoOpJobProcessor` — that integration (replacing the `JobProcessor` used by `SQSConsumer`) is deliberately left for a follow-up task after Week 1's individual pieces are reviewed, since it changes worker runtime behavior end-to-end and deserves its own dedicated review/test pass rather than being folded into Task 12. Marketing MCP is not called over the network by the worker. No Terraform, Kubernetes, or CI/CD changes are included.
