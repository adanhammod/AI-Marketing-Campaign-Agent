import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from campaign_contracts.artifacts import (
    AudioArtifactReference,
    FinalPackageArtifactReference,
    ImageArtifactReference,
    VideoArtifactReference,
)
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.artifact_store import PackageArtifactStore, StoredPackage

from .builder import build_package

_PACKAGE_FORMAT_VERSION = 1


def deterministic_package_artifact_id(campaign_id: UUID, campaign_version: int) -> UUID:
    return uuid5(campaign_id, f"version:{campaign_version}:package")


class PackageAssetPipeline(Protocol):
    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> FinalPackageArtifactReference: ...


def _image_key(artifact: ImageArtifactReference) -> str:
    prefix = f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}"
    return f"{prefix}/images/scene-{artifact.scene_number}.jpg"


def _audio_key(artifact: AudioArtifactReference) -> str:
    return f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}/audio/voiceover.mp3"


def _video_key(artifact: VideoArtifactReference) -> str:
    return f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}/video/final.mp4"


def _content_checksum(model: Any) -> str | None:
    if model is None:
        return None
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def _fingerprint(version: CampaignVersion) -> str:
    approval = version.approval
    payload = {
        "strategy_checksum": _content_checksum(version.strategy),
        "copy_checksum": _content_checksum(version.campaign_copy),
        "storyboard_checksum": _content_checksum(version.storyboard),
        "images": [
            {"scene_number": artifact.scene_number, "checksum": artifact.checksum_sha256}
            for artifact in sorted(version.image_artifacts, key=lambda a: a.scene_number or 0)
        ],
        "voice_checksum": version.voice_artifact.checksum_sha256 if version.voice_artifact else None,
        "video_checksum": version.video_artifact.checksum_sha256 if version.video_artifact else None,
        "approval_id": str(approval.approval_id) if approval is not None else None,
        "package_format_version": _PACKAGE_FORMAT_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _build_manifest(version: CampaignVersion) -> dict[str, Any]:
    approval = version.approval
    return {
        "campaign_id": str(version.campaign_id),
        "campaign_version": version.campaign_version,
        "title": f"{version.brief.business_name}: {version.brief.product_or_service}",
        "generated_at": datetime.now(UTC).isoformat(),
        "approval": (
            None
            if approval is None
            else {
                "approval_id": str(approval.approval_id),
                "approved_at": approval.approved_at.isoformat(),
                "note": approval.note,
            }
        ),
        "images": [
            {
                "scene_number": artifact.scene_number,
                "checksum_sha256": artifact.checksum_sha256,
                "provider": artifact.provider,
                "attribution": (None if artifact.attribution is None else artifact.attribution.attribution_text),
            }
            for artifact in sorted(version.image_artifacts, key=lambda a: a.scene_number or 0)
        ],
        "voice": (
            None
            if version.voice_artifact is None
            else {
                "checksum_sha256": version.voice_artifact.checksum_sha256,
                "provider": version.voice_artifact.provider,
            }
        ),
        "video": (
            None
            if version.video_artifact is None
            else {
                "checksum_sha256": version.video_artifact.checksum_sha256,
                "provider": version.video_artifact.provider,
            }
        ),
    }


class S3PackagePipeline:
    def __init__(
        self,
        s3_client: Any,
        store: PackageArtifactStore,
        artifact_bucket: str,
        *,
        max_download_bytes: int = 50_000_000,
        max_package_bytes: int = 52_428_800,
    ) -> None:
        self._s3 = s3_client
        self._store = store
        self._bucket = artifact_bucket
        self._max_download_bytes = max_download_bytes
        self._max_package_bytes = max_package_bytes

    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> FinalPackageArtifactReference:
        strategy = version.strategy
        campaign_copy = version.campaign_copy
        storyboard = version.storyboard
        if strategy is None or campaign_copy is None or storyboard is None:
            raise ValueError("package assembly requires strategy, campaign_copy, and storyboard")
        if len(version.image_artifacts) != 3:
            raise ValueError("package assembly requires exactly three image artifacts")
        if version.voice_artifact is None:
            raise ValueError("package assembly requires a voice artifact")
        if version.video_artifact is None:
            raise ValueError("package assembly requires a video artifact")

        fingerprint = _fingerprint(version)
        reconciled = self._store.reconcile_package(version, fingerprint)
        if reconciled is not None:
            return self._reference(version, reconciled)

        await self._checkpoint(is_cancelled, "before_download")
        images: list[tuple[int, bytes]] = []
        for artifact in sorted(version.image_artifacts, key=lambda a: a.scene_number or 0):
            data = self._download(_image_key(artifact))
            images.append((artifact.scene_number or 0, data))
            await self._checkpoint(is_cancelled, f"after_download_scene_{artifact.scene_number}")

        audio = self._download(_audio_key(version.voice_artifact))
        await self._checkpoint(is_cancelled, "after_download_audio")

        video = self._download(_video_key(version.video_artifact))
        await self._checkpoint(is_cancelled, "after_download_video")

        manifest = _build_manifest(version)

        await self._checkpoint(is_cancelled, "before_zip_assembly")
        built = build_package(
            campaign_version=version.campaign_version,
            strategy=strategy,
            campaign_copy=campaign_copy,
            storyboard=storyboard,
            images=images,
            audio=audio,
            video=video,
            manifest=manifest,
            max_size_bytes=self._max_package_bytes,
        )

        await self._checkpoint(is_cancelled, "before_s3_upload")
        metadata = {
            "campaign_id": str(version.campaign_id),
            "campaign_version": version.campaign_version,
            "package_fingerprint": fingerprint,
            "checksum_sha256": built.checksum_sha256,
            "size_bytes": built.size_bytes,
            "builder": "python-zipfile",
        }
        stored = self._store.put_package(version, built, metadata)
        return self._reference(version, stored)

    def _download(self, key: str) -> bytes:
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            data = response["Body"].read()
        except Exception as exc:
            raise WorkflowOperationError(
                "STORAGE_UNAVAILABLE", "artifact download unavailable", retryable=True
            ) from exc
        if not data:
            raise WorkflowOperationError("ARTIFACT_VALIDATION_FAILED", "downloaded artifact is empty", retryable=False)
        if len(data) > self._max_download_bytes:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED", "downloaded artifact exceeds size limit", retryable=False
            )
        return cast(bytes, data)

    @staticmethod
    async def _checkpoint(is_cancelled: Callable[[], Awaitable[bool]], phase: str) -> None:
        if await is_cancelled():
            raise NodeCancelled(f"prepare_final_package:{phase}")

    @staticmethod
    def _reference(version: CampaignVersion, stored: StoredPackage) -> FinalPackageArtifactReference:
        return FinalPackageArtifactReference(
            artifact_id=stored.artifact_id,
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.PACKAGE,
            mime_type="application/zip",
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.checksum_sha256,
            created_at=stored.created_at,
            provider="campaign-worker-package-builder",
        )
