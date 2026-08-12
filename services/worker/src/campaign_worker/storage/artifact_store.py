from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from campaign_contracts.artifacts import ArtifactAttribution
from campaign_contracts.campaign import CampaignVersion

from campaign_worker.audio.models import NormalizedAudio
from campaign_worker.images.models import NormalizedImage


@dataclass(frozen=True, slots=True)
class StoredImage:
    artifact_id: UUID
    checksum_sha256: str
    size_bytes: int
    created_at: datetime
    pexels_photo_id: int | None = None
    attribution: ArtifactAttribution | None = None


@dataclass(frozen=True, slots=True)
class StoredAudio:
    artifact_id: UUID
    checksum_sha256: str
    size_bytes: int
    created_at: datetime


class ArtifactStore(Protocol):
    def reconcile(self, version: CampaignVersion, scene_number: int, fingerprint: str) -> StoredImage | None: ...

    def put(
        self,
        version: CampaignVersion,
        scene_number: int,
        image: NormalizedImage,
        metadata: dict[str, object],
    ) -> StoredImage: ...


class AudioArtifactStore(Protocol):
    def reconcile_audio(self, version: CampaignVersion, fingerprint: str) -> StoredAudio | None: ...

    def put_audio(
        self,
        version: CampaignVersion,
        audio: NormalizedAudio,
        metadata: dict[str, object],
    ) -> StoredAudio: ...
