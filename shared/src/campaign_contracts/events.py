from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import Field
from .enums import Actor, CampaignEventType, CampaignStatus, WorkflowStep
from .validation import UTCModel
class CampaignEvent(UTCModel):
    event_id:UUID; campaign_id:UUID; campaign_version:int=Field(ge=1); event_sequence:int=Field(ge=1); event_type:CampaignEventType; status:CampaignStatus|None=None; step:WorkflowStep|None=None; progress_percent:int=Field(ge=0,le=100); occurred_at:datetime; actor:Actor; correlation_id:UUID; job_id:UUID|None=None; details:dict[str,Any]=Field(default_factory=dict)
class ProgressSummary(UTCModel):
    campaign_id:UUID; campaign_version:int=Field(ge=1); status:CampaignStatus; current_step:WorkflowStep|None=None; progress_percent:int=Field(ge=0,le=100); event_sequence:int=Field(ge=0); updated_at:datetime

def validate_event_progress(events:list[CampaignEvent])->None:
    ordered=sorted(events,key=lambda e:e.event_sequence)
    if len({e.event_sequence for e in ordered})!=len(ordered): raise ValueError("duplicate event sequence")
    for left,right in zip(ordered,ordered[1:]):
        if right.progress_percent<left.progress_percent: raise ValueError("progress cannot decrease")
