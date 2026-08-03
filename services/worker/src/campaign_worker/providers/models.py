from datetime import datetime
from uuid import UUID

from campaign_contracts.artifacts import ImageArtifactReference, PublicArtifactReference, VideoArtifactReference
from campaign_contracts.campaign import ImagePrompt, Storyboard
from campaign_contracts.errors import SanitizedWorkflowError
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ASPECT_RATIO_9_16 = "9:16"


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    campaign_version: int = Field(ge=1)
    prompt: ImagePrompt

    @model_validator(mode="after")
    def _validate_aspect_ratio(self) -> "ImageGenerationRequest":
        if self.prompt.aspect_ratio != _ASPECT_RATIO_9_16:
            raise ValueError(f"aspect_ratio must be {_ASPECT_RATIO_9_16} for MVP scope")
        return self


class ImageGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    fallback_asset: bool = False
    started_at: datetime
    completed_at: datetime
    artifact: ImageArtifactReference | None = None
    error: SanitizedWorkflowError | None = None


class VideoRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    campaign_version: int = Field(ge=1)
    storyboard: Storyboard
    image_artifacts: list[ImageArtifactReference] = Field(default_factory=list)
    voice_artifact: PublicArtifactReference | None = None
    aspect_ratio: str = Field(default=_ASPECT_RATIO_9_16, pattern=r"^9:16$")


class VideoRenderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    fallback_asset: bool = False
    started_at: datetime
    completed_at: datetime
    artifact: VideoArtifactReference | None = None
    error: SanitizedWorkflowError | None = None
