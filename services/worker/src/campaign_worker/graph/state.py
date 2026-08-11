from typing import NotRequired, TypedDict
from uuid import UUID

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
    # Set once, by GraphJobProcessor.process, from the SQS message's own correlation_id --
    # every node reads it (via with_step_tracking) to tag STEP_STARTED/STEP_COMPLETED
    # events without threading a new parameter through every graph-construction call site.
    correlation_id: NotRequired[UUID]
