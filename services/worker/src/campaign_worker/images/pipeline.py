import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID, uuid5

from campaign_contracts.artifacts import ImageArtifactReference
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.artifact_store import ArtifactStore

from .models import PhotoCandidate, QueryGenerationResult
from .processor import ImageProcessor
from .selector import rank_candidates


class ImageSearchQueryGenerator(Protocol):
    async def generate(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> QueryGenerationResult: ...


class PhotoClient(Protocol):
    async def search(self, query: str) -> list[PhotoCandidate]: ...

    async def download(self, photo: PhotoCandidate, max_bytes: int) -> bytes: ...


class ImageAssetPipeline(Protocol):
    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> list[ImageArtifactReference]: ...


def deterministic_artifact_id(campaign_id: UUID, version: int, scene_number: int) -> UUID:
    return uuid5(campaign_id, f"version:{version}:scene:{scene_number}:image")


def campaign_storyboard_fingerprint(version: CampaignVersion) -> str:
    if version.storyboard is None:
        raise ValueError("image acquisition requires a storyboard")
    value = {
        "brief": version.brief.model_dump(mode="json"),
        "storyboard": version.storyboard.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class StockImagePipeline:
    def __init__(
        self,
        query_generator: ImageSearchQueryGenerator,
        photos: PhotoClient,
        processor: ImageProcessor,
        store: ArtifactStore,
    ) -> None:
        self._query_generator = query_generator
        self._photos = photos
        self._processor = processor
        self._store = store

    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> list[ImageArtifactReference]:
        if version.storyboard is None:
            raise ValueError("image acquisition requires a storyboard")
        fingerprint = campaign_storyboard_fingerprint(version)
        stored = {
            scene.scene_number: self._store.reconcile(version, scene.scene_number, fingerprint)
            for scene in version.storyboard.scenes
        }
        if all(stored.values()):
            return [self._reference(version, n, stored[n]) for n in (1, 2, 3) if stored[n] is not None]

        query_result = await self._query_generator.generate(version, is_cancelled)
        used_ids: set[int] = {
            item.pexels_photo_id for item in stored.values() if item is not None and item.pexels_photo_id is not None
        }
        completed = dict(stored)
        for query in query_result.queries:
            if completed[query.scene_number] is not None:
                continue
            await self._checkpoint(is_cancelled, "between_scenes")
            await self._checkpoint(is_cancelled, "before_pexels_search")
            candidates = await self._photos.search(query.query)
            ranked = rank_candidates(candidates, used_ids)
            if not ranked:
                raise WorkflowOperationError(
                    "INVALID_PROVIDER_OUTPUT", "Pexels returned no distinct photos", retryable=True
                )
            last_error: WorkflowOperationError | None = None
            for photo in ranked:
                try:
                    await self._checkpoint(is_cancelled, "before_download")
                    source = await self._photos.download(photo, self._processor.max_download_bytes)
                    await self._checkpoint(is_cancelled, "before_image_processing")
                    image = self._processor.normalize(source)
                    await self._checkpoint(is_cancelled, "before_s3_upload")
                    metadata: dict[str, object] = {
                        "campaign_id": str(version.campaign_id),
                        "campaign_version": version.campaign_version,
                        "scene_number": query.scene_number,
                        "storyboard_fingerprint": fingerprint,
                        "pexels_photo_id": photo.photo_id,
                        "photographer_name": photo.photographer,
                        "photographer_id": photo.photographer_id or 0,
                        "photographer_profile_url": photo.photographer_url,
                        "pexels_photo_page_url": photo.photo_page_url,
                        "original_width": photo.width,
                        "original_height": photo.height,
                        "source_variant": photo.source_variant,
                        "alt_text": photo.alt_text,
                        "search_query": query.query,
                        "source_checksum": image.source_checksum_sha256,
                        "normalized_checksum": image.checksum_sha256,
                        "query_generator_provider": query_result.provider,
                        "query_generator_model": query_result.model or "",
                        "fallback_query_generation": query_result.fallback_used,
                    }
                    completed[query.scene_number] = self._store.put(version, query.scene_number, image, metadata)
                    used_ids.add(photo.photo_id)
                    break
                except WorkflowOperationError as exc:
                    last_error = exc
                    if exc.code == "STORAGE_UNAVAILABLE":
                        raise
            if completed[query.scene_number] is None:
                if last_error is not None:
                    raise last_error
                raise WorkflowOperationError(
                    "INVALID_PROVIDER_OUTPUT", "no valid Pexels image candidate", retryable=True
                )
        if any(completed[n] is None for n in (1, 2, 3)):
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "image acquisition did not produce three images", retryable=True
            )
        return [self._reference(version, n, completed[n]) for n in (1, 2, 3) if completed[n] is not None]

    @staticmethod
    async def _checkpoint(is_cancelled: Callable[[], Awaitable[bool]], phase: str) -> None:
        if await is_cancelled():
            raise NodeCancelled(f"generate_images:{phase}")

    @staticmethod
    def _reference(version: CampaignVersion, scene_number: int, stored: object) -> ImageArtifactReference:
        from campaign_worker.storage.artifact_store import StoredImage

        assert isinstance(stored, StoredImage)
        return ImageArtifactReference(
            artifact_id=stored.artifact_id,
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/jpeg",
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.checksum_sha256,
            scene_number=scene_number,
            attribution=stored.attribution,
            created_at=stored.created_at,
            provider="pexels",
        )
