from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, ConfigDict, field_validator
from .enums import CampaignStatus

class ContractModel(BaseModel):
    model_config=ConfigDict(extra="forbid", strict=False)
class UTCModel(ContractModel):
    @field_validator("*", mode="after")
    @classmethod
    def utc(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset()!=timezone.utc.utcoffset(value)):
            raise ValueError("datetime must be timezone-aware UTC")
        return value
TRANSITIONS={
 CampaignStatus.CREATED:frozenset({CampaignStatus.QUEUED,CampaignStatus.FAILED,CampaignStatus.CANCELLED}),
 CampaignStatus.QUEUED:frozenset({CampaignStatus.GENERATING_STRATEGY,CampaignStatus.GENERATING_COPY,CampaignStatus.GENERATING_STORYBOARD,CampaignStatus.GENERATING_IMAGES,CampaignStatus.RENDERING_VIDEO,CampaignStatus.FAILED,CampaignStatus.CANCELLED}),
 CampaignStatus.GENERATING_STRATEGY:frozenset({CampaignStatus.GENERATING_COPY,CampaignStatus.FAILED,CampaignStatus.CANCELLED}),
 CampaignStatus.GENERATING_COPY:frozenset({CampaignStatus.GENERATING_STORYBOARD,CampaignStatus.FAILED,CampaignStatus.CANCELLED}),
 CampaignStatus.GENERATING_STORYBOARD:frozenset({CampaignStatus.GENERATING_IMAGES,CampaignStatus.FAILED,CampaignStatus.CANCELLED}),
 CampaignStatus.GENERATING_IMAGES:frozenset({CampaignStatus.RENDERING_VIDEO,CampaignStatus.FAILED,CampaignStatus.CANCELLED}),
 CampaignStatus.RENDERING_VIDEO:frozenset({CampaignStatus.READY_FOR_REVIEW,CampaignStatus.FAILED,CampaignStatus.CANCELLED}),
 CampaignStatus.READY_FOR_REVIEW:frozenset({CampaignStatus.APPROVED,CampaignStatus.REVISION_REQUESTED,CampaignStatus.CANCELLED}),
 CampaignStatus.APPROVED:frozenset({CampaignStatus.FINAL,CampaignStatus.FAILED}), CampaignStatus.FAILED:frozenset({CampaignStatus.QUEUED}),
 CampaignStatus.REVISION_REQUESTED:frozenset(), CampaignStatus.FINAL:frozenset(), CampaignStatus.CANCELLED:frozenset()}
TERMINAL_STATES=frozenset({CampaignStatus.REVISION_REQUESTED,CampaignStatus.FINAL,CampaignStatus.FAILED,CampaignStatus.CANCELLED})
CANCELLABLE_STATES=frozenset({CampaignStatus.CREATED,CampaignStatus.QUEUED,CampaignStatus.GENERATING_STRATEGY,CampaignStatus.GENERATING_COPY,CampaignStatus.GENERATING_STORYBOARD,CampaignStatus.GENERATING_IMAGES,CampaignStatus.RENDERING_VIDEO,CampaignStatus.READY_FOR_REVIEW})
def is_legal_transition(a,b): return b in TRANSITIONS[a]
def validate_transition(a,b):
    if not is_legal_transition(a,b): raise ValueError(f"illegal campaign transition: {a} -> {b}")
def is_terminal(s): return s in TERMINAL_STATES
def is_user_action_transition(a,b): return (a,b) in {(CampaignStatus.READY_FOR_REVIEW,CampaignStatus.APPROVED),(CampaignStatus.READY_FOR_REVIEW,CampaignStatus.REVISION_REQUESTED),(CampaignStatus.FAILED,CampaignStatus.QUEUED)} or (b==CampaignStatus.CANCELLED and a in CANCELLABLE_STATES)
def is_retryable_state(s): return s==CampaignStatus.FAILED
def is_cancellable_state(s): return s in CANCELLABLE_STATES
