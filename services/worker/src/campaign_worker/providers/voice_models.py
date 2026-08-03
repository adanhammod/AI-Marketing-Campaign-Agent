from datetime import datetime
from uuid import UUID

from campaign_contracts.artifacts import PublicArtifactReference
from campaign_contracts.errors import SanitizedWorkflowError
from pydantic import BaseModel, ConfigDict, Field


class VoiceGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    campaign_version: int = Field(ge=1)
    narration_text: str = Field(min_length=1, max_length=4000)


class VoiceGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    fallback_asset: bool = False
    started_at: datetime
    completed_at: datetime
    artifact: PublicArtifactReference | None = None
    error: SanitizedWorkflowError | None = None
