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
