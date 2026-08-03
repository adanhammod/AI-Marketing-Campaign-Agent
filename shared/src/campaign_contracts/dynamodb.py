from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from .campaign import (
    ApprovalRecord,
    CampaignAggregateMetadata,
    CampaignVersion,
)
from .enums import WorkflowStep
from .events import CampaignEvent
from .steps import WorkflowStepRecord


def pk(campaign_id: UUID) -> str:
    return f"CAMPAIGN#{campaign_id}"


def meta_sk() -> str:
    return "META"


def version_sk(version: int) -> str:
    if version < 1:
        raise ValueError("version must be positive")
    return f"VERSION#{version}"


def step_sk(version: int, step: WorkflowStep) -> str:
    return f"STEP#{version}#{step.value}"


def event_sk(sequence: int, event_id: UUID) -> str:
    if sequence < 1:
        raise ValueError("sequence must be positive")
    return f"EVENT#{sequence:020d}#{event_id}"


def approval_sk(version: int) -> str:
    return f"APPROVAL#{version}"


def _ddb(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        raise TypeError("floats are forbidden in DynamoDB contracts")
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_ddb(x) for x in value]
    if isinstance(value, dict):
        return {k: _ddb(v) for k, v in value.items() if v is not None}
    return value


def serialize(model, entity_type: str, sk: str) -> dict[str, Any]:
    body = _ddb(model.model_dump(mode="python", exclude_none=True, by_alias=True))
    return {"PK": pk(model.campaign_id), "SK": sk, "entity_type": entity_type, **body}


def serialize_meta(m: CampaignAggregateMetadata):
    return serialize(m, "CAMPAIGN", meta_sk())


def serialize_version(v: CampaignVersion):
    return serialize(v, "CAMPAIGN_VERSION", version_sk(v.campaign_version))


def serialize_event(e: CampaignEvent):
    return serialize(e, "CAMPAIGN_EVENT", event_sk(e.event_sequence, e.event_id))


def serialize_approval(a: ApprovalRecord):
    return serialize(a, "APPROVAL", approval_sk(a.campaign_version))


def serialize_step(step: WorkflowStepRecord) -> dict[str, Any]:
    return serialize(step, "WORKFLOW_STEP", step_sk(step.campaign_version, step.step))
