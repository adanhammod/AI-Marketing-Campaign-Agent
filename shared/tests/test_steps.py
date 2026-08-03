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
