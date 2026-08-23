from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import Field, field_validator
from .enums import ErrorComponent, WorkflowStep
from .validation import UTCModel
ERROR_CODES={"VALIDATION_ERROR","NOT_FOUND","STATE_CONFLICT","IDEMPOTENCY_CONFLICT","UNSUPPORTED_MESSAGE_SCHEMA","LEASE_CONFLICT","RETRY_EXHAUSTED","DELIVERY_RETRY_EXHAUSTED","CANCELLED_BY_USER","BEDROCK_UNAVAILABLE","IMAGE_PROVIDER_UNAVAILABLE","VIDEO_PROVIDER_UNAVAILABLE","PROVIDER_POLICY_REJECTION","PROVIDER_THROTTLED","PROVIDER_TIMEOUT","INVALID_PROVIDER_OUTPUT","ARTIFACT_VALIDATION_FAILED","STORAGE_UNAVAILABLE","QUEUE_UNAVAILABLE","PACKAGE_VALIDATION_FAILED","INTERNAL_ERROR"}
class ProviderStatus(UTCModel):
    provider:str=Field(min_length=1,max_length=80); category:str=Field(min_length=1,max_length=50); http_status:int|None=Field(default=None,ge=100,le=599); provider_code:str|None=Field(default=None,max_length=100)
class SanitizedWorkflowError(UTCModel):
    schema_version:int=Field(default=1,ge=1,le=1); code:str; message:str=Field(min_length=1,max_length=500); component:ErrorComponent; workflow_step:WorkflowStep|None=None; attempt:int=Field(ge=1); retryable:bool; timestamp:datetime; correlation_id:UUID; campaign_id:UUID|None=None; campaign_version:int|None=Field(default=None,ge=1); job_id:UUID|None=None; provider_status:ProviderStatus|None=None
    @field_validator("code")
    @classmethod
    def stable_code(cls,v):
        if v not in ERROR_CODES: raise ValueError("unknown error code")
        return v
class ErrorEnvelope(UTCModel): error:SanitizedWorkflowError
class StandardValidationError(ErrorEnvelope): pass
class ConflictError(ErrorEnvelope): pass
class NotFoundError(ErrorEnvelope): pass
