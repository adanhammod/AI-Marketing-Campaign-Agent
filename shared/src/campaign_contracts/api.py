from datetime import datetime
from uuid import UUID
from pydantic import Field, model_validator
from .artifacts import PublicArtifactReference
from .campaign import ApprovalRecord, CampaignCopy, NormalizedCampaignBrief, PublicCampaignVersion, RevisionFeedback, Storyboard, StrategyOutput
from .enums import CampaignStatus, RevisionTarget, WorkflowStep
from .errors import ConflictError, NotFoundError, SanitizedWorkflowError, StandardValidationError
from .events import CampaignEvent
from .validation import UTCModel
class CampaignCreationRequest(NormalizedCampaignBrief): pass
class CampaignCreationAcceptedResponse(UTCModel): campaign_id:UUID; campaign_version:int=Field(ge=1); job_id:UUID; status:CampaignStatus; progress_percent:int=Field(ge=0,le=100); links:dict[str,str]
class CampaignSummary(UTCModel): campaign_id:UUID; title:str; current_version:int=Field(ge=1); latest_final_version:int|None=Field(default=None,ge=1); status:CampaignStatus; current_step:WorkflowStep|None=None; progress_percent:int=Field(ge=0,le=100); created_at:datetime; updated_at:datetime
class CampaignListResponse(UTCModel): items:list[CampaignSummary]; next_cursor:str|None=None
class CampaignDetailResponse(PublicCampaignVersion): title:str; current_version:int=Field(ge=1); latest_final_version:int|None=Field(default=None,ge=1); event_sequence:int=Field(ge=0); review_manifest_checksum:str|None=Field(default=None,pattern=r"^[0-9a-f]{64}$"); actions:dict[str,str]=Field(default_factory=dict)
class CampaignEventsResponse(UTCModel): campaign_id:UUID; campaign_version:int=Field(ge=1); items:list[CampaignEvent]; next_cursor:str|None=None; latest_sequence:int=Field(ge=0); terminal:bool
class CampaignArtifactsResponse(UTCModel): campaign_id:UUID; campaign_version:int=Field(ge=1); items:list[PublicArtifactReference]
class RevisionRequest(UTCModel):
    reason:str=Field(min_length=1,max_length=1000); scope:RevisionTarget; affected_artifact_ids:list[UUID]=Field(default_factory=list)
    @model_validator(mode="after")
    def selected(self):
        if self.scope==RevisionTarget.SELECTED_IMAGES and not self.affected_artifact_ids: raise ValueError("selected images require artifact IDs")
        if self.scope!=RevisionTarget.SELECTED_IMAGES and self.affected_artifact_ids: raise ValueError("artifact IDs only allowed for selected images")
        return self
class RevisionResponse(UTCModel): campaign_id:UUID; parent_version:int=Field(ge=1); campaign_version:int=Field(ge=2); job_id:UUID; status:CampaignStatus; earliest_affected_step:WorkflowStep
class RetryRequest(UTCModel): pass
class RetryResponse(UTCModel): campaign_id:UUID; campaign_version:int=Field(ge=1); job_id:UUID; status:CampaignStatus; resume_step:WorkflowStep; attempt:int=Field(ge=1)
class CancellationRequest(UTCModel): reason:str=Field(min_length=1,max_length=500)
class CancellationResponse(UTCModel): campaign_id:UUID; campaign_version:int=Field(ge=1); status:CampaignStatus; cancellation_pending:bool=False
__all__=["CampaignCreationRequest","CampaignCreationAcceptedResponse","CampaignListResponse","CampaignDetailResponse","CampaignEventsResponse","CampaignArtifactsResponse","RevisionRequest","RevisionResponse","RetryRequest","RetryResponse","CancellationRequest","CancellationResponse","StandardValidationError","ConflictError","NotFoundError"]
