from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, HttpUrl, field_validator, model_validator

from .enums import ArtifactType, WorkflowStep
from .validation import UTCModel

SHA256 = r"^[0-9a-f]{64}$"


class ArtifactReference(UTCModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    artifact_id: UUID
    artifact_type: ArtifactType
    campaign_id: UUID
    campaign_version: int = Field(ge=1)
    workflow_step: WorkflowStep
    storage_namespace: str = Field(min_length=1, max_length=100)
    s3_bucket: str = Field(min_length=3, max_length=63)
    s3_key: str = Field(min_length=1, max_length=1024)
    mime_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=SHA256)
    created_at: datetime
    provider: str | None = Field(default=None, max_length=80)
    provider_artifact_id: str | None = Field(default=None, max_length=200)
    generation_summary: dict[str, Any] = Field(default_factory=dict)
    presigned_url_expires_at: None = None

    @field_validator("generation_summary")
    @classmethod
    def safe_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        banned = {"prompt", "token", "secret", "url", "raw_response", "provider_payload"}
        if banned.intersection(map(str.lower, value)):
            raise ValueError("generation summary contains unsafe field")
        return value


class ArtifactAttribution(UTCModel):
    provider_asset_id: str = Field(min_length=1, max_length=200)
    creator_name: str = Field(min_length=1, max_length=200)
    creator_profile_url: HttpUrl
    source_page_url: HttpUrl
    provider_url: HttpUrl
    attribution_text: str = Field(min_length=1, max_length=500)

    @field_validator("creator_profile_url", "source_page_url", "provider_url")
    @classmethod
    def https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("public attribution URLs must use HTTPS")
        return value


class PublicArtifactReference(UTCModel):
    artifact_id: UUID
    artifact_type: ArtifactType
    campaign_id: UUID
    campaign_version: int = Field(ge=1)
    workflow_step: WorkflowStep
    mime_type: str
    size_bytes: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=SHA256)
    created_at: datetime
    provider: str | None = None
    scene_number: int | None = Field(default=None, ge=1, le=3)
    attribution: ArtifactAttribution | None = None
    download_url: HttpUrl | None = None
    download_url_expires_at: datetime | None = None

    @field_validator("download_url")
    @classmethod
    def download_https_only(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("download URL must use HTTPS")
        return value

    @model_validator(mode="after")
    def download_fields_match(self) -> "PublicArtifactReference":
        if (self.download_url is None) != (self.download_url_expires_at is None):
            raise ValueError("download URL and expiration must be provided together")
        return self


class PresignedURLMetadata(UTCModel):
    artifact_id: UUID
    download_url: str = Field(pattern=r"^https://")
    download_url_expires_at: datetime


class ImageArtifactReference(PublicArtifactReference):
    artifact_type: ArtifactType = ArtifactType.IMAGE


class VideoArtifactReference(PublicArtifactReference):
    artifact_type: ArtifactType = ArtifactType.VIDEO
