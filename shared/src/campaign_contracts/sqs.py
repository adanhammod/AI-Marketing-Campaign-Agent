from datetime import datetime
from uuid import UUID
from pydantic import Field, model_validator
from .enums import RevisionTarget, SQSOperation, WorkflowStep
from .validation import UTCModel
class SQSJobMessage(UTCModel):
    schema_version:int=Field(ge=1); job_id:UUID; campaign_id:UUID; campaign_version:int=Field(ge=1); operation:SQSOperation; requested_step:WorkflowStep|None=None; revision_scope:RevisionTarget|None=None; idempotency_key:str=Field(min_length=1,max_length=128,pattern=r"^[ -~]+$"); correlation_id:UUID; requested_at:datetime; attempt:int=Field(ge=0); trace_id:str|None=Field(default=None,pattern=r"^[0-9a-f]{32}$")
    @model_validator(mode="after")
    def supported(self):
        if self.schema_version!=1: raise ValueError("unsupported schema_version")
        if self.operation==SQSOperation.START and (self.requested_step is not None or self.revision_scope is not None): raise ValueError("START excludes requested_step and revision_scope")
        if self.operation==SQSOperation.REGENERATE and (self.requested_step is None or self.revision_scope is None): raise ValueError("REGENERATE requires requested_step and revision_scope")
        if self.operation!=SQSOperation.REGENERATE and self.revision_scope is not None: raise ValueError("revision_scope requires REGENERATE")
        return self

def duplicate_delivery_key(message:SQSJobMessage)->tuple[UUID,int,SQSOperation,str]: return (message.campaign_id,message.campaign_version,message.operation,message.idempotency_key)
