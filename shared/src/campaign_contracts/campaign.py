from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import Field, HttpUrl, model_validator
from .artifacts import ImageArtifactReference, VideoArtifactReference, PublicArtifactReference
from .enums import CampaignStatus, RevisionTarget, WorkflowStep
from .errors import SanitizedWorkflowError
from .validation import UTCModel
class NormalizedCampaignBrief(UTCModel):
    business_name:str=Field(min_length=2,max_length=120); product_or_service:str=Field(min_length=2,max_length=200); business_description:str=Field(min_length=20,max_length=2000); campaign_goal:str=Field(min_length=3,max_length=300); platforms:list[str]=Field(min_length=1,max_length=5); tone:str=Field(min_length=2,max_length=80); language:str=Field(min_length=2,max_length=20); target_audience:str|None=Field(default=None,max_length=1000); key_message:str|None=Field(default=None,max_length=500); call_to_action:str|None=Field(default=None,max_length=200); brand_colors:list[str]=Field(default_factory=list,max_length=5); reference_artifact_id:UUID|None=None
class CampaignConstraints(UTCModel):
    image_count:int=Field(default=3,ge=3,le=3); scene_count:int=Field(default=3,ge=3,le=3); target_duration_seconds:int=Field(default=15,ge=15,le=15); min_duration_seconds:int=Field(default=13,ge=13,le=13); max_duration_seconds:int=Field(default=17,ge=17,le=17); aspect_ratio:str=Field(default="9:16",pattern=r"^9:16$"); preferred_resolution:str="1080x1920"; fallback_resolution:str="720x1280"; output_format:str="MP4"; video_codec:str="H.264"; audio_codec:str="AAC"
class StrategyOutput(UTCModel): audience:str; positioning:str; objective:str; key_message:str; channel_rationale:dict[str,str]
class ChannelCopy(UTCModel): channel:str; headline:str; caption:str; call_to_action:str; hashtags:list[str]
class CampaignCopy(UTCModel): headline:str; caption:str; call_to_action:str; hashtags:list[str]; channel_variants:list[ChannelCopy]
class StoryboardScene(UTCModel): scene_number:int=Field(ge=1,le=3); purpose:str; duration_seconds:int=Field(ge=1); narration:str; text_overlay:str=""; visual_prompt:str; transition:str
class Storyboard(UTCModel):
    scenes:list[StoryboardScene]=Field(min_length=3,max_length=3); total_duration_seconds:int=Field(ge=13,le=17)
    @model_validator(mode="after")
    def sequence(self):
        if [s.scene_number for s in self.scenes]!=[1,2,3] or sum(s.duration_seconds for s in self.scenes)!=self.total_duration_seconds: raise ValueError("invalid scene ordering or duration")
        return self
class ImagePrompt(UTCModel): scene_number:int=Field(ge=1,le=3); prompt:str=Field(min_length=1,max_length=4000); aspect_ratio:str="9:16"; reference_artifact_id:UUID|None=None
class ReviewPackage(UTCModel): artifact_id:UUID; manifest_checksum:str=Field(pattern=r"^[0-9a-f]{64}$"); artifact_ids:list[UUID]=Field(min_length=1)
class RevisionFeedback(UTCModel): parent_version:int=Field(ge=1); reason:str=Field(min_length=1,max_length=1000); scope:RevisionTarget; affected_artifact_ids:list[UUID]=Field(default_factory=list); earliest_affected_step:WorkflowStep
class ApprovalRecord(UTCModel): approval_id:UUID; campaign_id:UUID; campaign_version:int=Field(ge=1); decision:str=Field(default="APPROVED",pattern="^APPROVED$"); approved_at:datetime; actor:str=Field(default="DEMO_USER",pattern="^DEMO_USER$"); manifest_checksum:str=Field(pattern=r"^[0-9a-f]{64}$"); note:str|None=Field(default=None,max_length=500); created_at:datetime; lock_version:int=Field(default=1,ge=1)
class RetryMetadata(UTCModel): attempt:int=Field(default=0,ge=0); max_attempts:int=Field(default=3,ge=1); retryable:bool=False; resume_step:WorkflowStep|None=None
class ProcessingLease(UTCModel): owner:str; acquired_at:datetime; expires_at:datetime; heartbeat_at:datetime
class WorkflowCheckpoint(UTCModel): checkpoint_version:int=Field(ge=0); step:WorkflowStep|None=None; s3_reference:str|None=None; output_checksum:str|None=Field(default=None,pattern=r"^[0-9a-f]{64}$")
class CampaignAggregateMetadata(UTCModel): campaign_id:UUID; current_version:int=Field(ge=1); latest_final_version:int|None=Field(default=None,ge=1); title:str; created_at:datetime; updated_at:datetime; lock_version:int=Field(ge=0); event_sequence:int=Field(default=0,ge=0); current_status:CampaignStatus|None=None; current_progress:int|None=Field(default=None,ge=0,le=100)
class CampaignVersion(UTCModel):
    schema_version:int=Field(default=1,ge=1,le=1); campaign_id:UUID; campaign_version:int=Field(ge=1); parent_version:int|None=Field(default=None,ge=1); job_id:UUID; status:CampaignStatus; current_step:WorkflowStep|None=None; progress_percent:int=Field(ge=0,le=100); brief:NormalizedCampaignBrief; constraints:CampaignConstraints; strategy:StrategyOutput|None=None; campaign_copy:CampaignCopy|None=Field(default=None,alias="copy",serialization_alias="copy"); storyboard:Storyboard|None=None; image_prompts:list[ImagePrompt]=Field(default_factory=list); image_artifacts:list[ImageArtifactReference]=Field(default_factory=list); video_artifact:VideoArtifactReference|None=None; review_package:ReviewPackage|None=None; revision:RevisionFeedback|None=None; approval:ApprovalRecord|None=None; completed_steps:list[WorkflowStep]=Field(default_factory=list); retry:RetryMetadata; error:SanitizedWorkflowError|None=None; created_at:datetime; updated_at:datetime; lock_version:int=Field(default=0,ge=0); checkpoint_version:int=Field(default=0,ge=0)
    @model_validator(mode="after")
    def version_parent(self):
        if (self.campaign_version==1 and self.parent_version is not None) or (self.campaign_version>1 and self.parent_version!=self.campaign_version-1): raise ValueError("parent_version must identify the immediate predecessor")
        return self
class TypedLangGraphCampaignState(CampaignVersion): lease:ProcessingLease|None=None; checkpoint:WorkflowCheckpoint|None=None; provider_job_ids:dict[str,str]=Field(default_factory=dict); idempotency_keys:dict[str,str]=Field(default_factory=dict)
class PublicCampaignVersion(UTCModel): campaign_id:UUID; campaign_version:int; status:CampaignStatus; current_step:WorkflowStep|None=None; progress_percent:int; brief:NormalizedCampaignBrief; strategy:StrategyOutput|None=None; campaign_copy:CampaignCopy|None=Field(default=None,alias="copy",serialization_alias="copy"); storyboard:Storyboard|None=None; artifacts:list[PublicArtifactReference]=Field(default_factory=list); revision:RevisionFeedback|None=None; approval:ApprovalRecord|None=None; completed_steps:list[WorkflowStep]=Field(default_factory=list); retry_eligible:bool; error:SanitizedWorkflowError|None=None; created_at:datetime; updated_at:datetime

def assert_version_immutable(before:CampaignVersion,after:CampaignVersion)->None:
    if before.status in {CampaignStatus.REVISION_REQUESTED,CampaignStatus.FINAL,CampaignStatus.CANCELLED} and before!=after: raise ValueError("immutable campaign version changed")
    frozen={"brief","constraints","strategy","campaign_copy","storyboard","image_prompts","image_artifacts","video_artifact","review_package"}
    if before.status==CampaignStatus.READY_FOR_REVIEW and any(getattr(before,x)!=getattr(after,x) for x in frozen): raise ValueError("review output is immutable")

REGENERATION_STEPS={RevisionTarget.STRATEGY:WorkflowStep.STRATEGY,RevisionTarget.COPY:WorkflowStep.COPY,RevisionTarget.STORYBOARD:WorkflowStep.STORYBOARD,RevisionTarget.SELECTED_IMAGES:WorkflowStep.IMAGES,RevisionTarget.VIDEO:WorkflowStep.VIDEO}
def validate_approval_target(aggregate:CampaignAggregateMetadata,version:CampaignVersion)->None:
    if aggregate.campaign_id!=version.campaign_id or aggregate.current_version!=version.campaign_version or version.status!=CampaignStatus.READY_FOR_REVIEW: raise ValueError("approval must target the current READY_FOR_REVIEW version")
def earliest_regeneration_step(target:RevisionTarget)->WorkflowStep:return REGENERATION_STEPS[target]
