from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import Field, field_validator
from .enums import ArtifactType, WorkflowStep
from .validation import UTCModel
SHA256=r"^[0-9a-f]{64}$"
class ArtifactReference(UTCModel):
    schema_version:int=Field(default=1,ge=1,le=1); artifact_id:UUID; artifact_type:ArtifactType; campaign_id:UUID; campaign_version:int=Field(ge=1); workflow_step:WorkflowStep; storage_namespace:str=Field(min_length=1,max_length=100); s3_bucket:str=Field(min_length=3,max_length=63); s3_key:str=Field(min_length=1,max_length=1024); mime_type:str=Field(min_length=3,max_length=100); size_bytes:int=Field(ge=0); checksum_sha256:str=Field(pattern=SHA256); created_at:datetime; provider:str|None=Field(default=None,max_length=80); provider_artifact_id:str|None=Field(default=None,max_length=200); generation_summary:dict[str,Any]=Field(default_factory=dict); presigned_url_expires_at:None=None
    @field_validator("generation_summary")
    @classmethod
    def safe_summary(cls,v):
        banned={"prompt","token","secret","url","raw_response","provider_payload"}
        if banned.intersection(map(str.lower,v)): raise ValueError("generation summary contains unsafe field")
        return v
class PublicArtifactReference(UTCModel):
    artifact_id:UUID; artifact_type:ArtifactType; campaign_id:UUID; campaign_version:int=Field(ge=1); workflow_step:WorkflowStep; mime_type:str; size_bytes:int=Field(ge=0); checksum_sha256:str=Field(pattern=SHA256); created_at:datetime; provider:str|None=None
class PresignedURLMetadata(UTCModel):
    artifact_id:UUID; download_url:str=Field(pattern=r"^https://"); download_url_expires_at:datetime
class ImageArtifactReference(PublicArtifactReference): artifact_type:ArtifactType=ArtifactType.IMAGE
class VideoArtifactReference(PublicArtifactReference): artifact_type:ArtifactType=ArtifactType.VIDEO
