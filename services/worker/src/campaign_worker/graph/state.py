from typing import NotRequired, TypedDict

from campaign_contracts.artifacts import PublicArtifactReference
from campaign_contracts.campaign import CampaignVersion
from pydantic import BaseModel, ConfigDict, Field


class ReviewPackageValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    missing_artifacts: list[str] = Field(default_factory=list)


class GraphState(TypedDict):
    version: CampaignVersion
    voice_artifact: NotRequired[PublicArtifactReference]
    review_validation: NotRequired[ReviewPackageValidationResult]
