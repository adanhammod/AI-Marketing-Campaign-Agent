import hashlib
import json
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid5

from campaign_contracts.artifacts import AudioArtifactReference, ImageArtifactReference, VideoArtifactReference
from campaign_contracts.campaign import CampaignVersion, Storyboard
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.artifact_store import StoredVideo, VideoArtifactStore

from .compositor import build_render_args
from .ffmpeg_runner import run_ffmpeg, run_ffprobe
from .models import RenderedVideo

_RENDER_SETTINGS_VERSION = 1
_MIN_SCALE = 0.85
_MAX_SCALE = 1.25
_TARGET_WIDTH = 1080
_TARGET_HEIGHT = 1920
_TARGET_FPS = 30


def deterministic_video_artifact_id(campaign_id: UUID, campaign_version: int) -> UUID:
    return uuid5(campaign_id, f"version:{campaign_version}:video")


class VideoAssetPipeline(Protocol):
    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> VideoArtifactReference: ...


def _image_key(artifact: ImageArtifactReference) -> str:
    prefix = f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}"
    return f"{prefix}/images/scene-{artifact.scene_number}.jpg"


def _audio_key(artifact: AudioArtifactReference) -> str:
    return f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}/audio/voiceover.mp3"


def _fingerprint(version: CampaignVersion, storyboard: Storyboard) -> str:
    payload = {
        "images": [
            {
                "artifact_id": str(artifact.artifact_id),
                "checksum": artifact.checksum_sha256,
                "scene_number": artifact.scene_number,
            }
            for artifact in sorted(version.image_artifacts, key=lambda a: a.scene_number or 0)
        ],
        "voice_checksum": version.voice_artifact.checksum_sha256 if version.voice_artifact else None,
        "scene_durations": [
            scene.duration_seconds for scene in sorted(storyboard.scenes, key=lambda s: s.scene_number)
        ],
        "aspect_ratio": version.constraints.aspect_ratio,
        "render_settings_version": _RENDER_SETTINGS_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class FfmpegVideoPipeline:
    def __init__(
        self,
        s3_client: Any,
        store: VideoArtifactStore,
        artifact_bucket: str,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        render_timeout_seconds: float = 60,
        max_download_bytes: int = 50_000_000,
        ffmpeg_runner: Callable[..., Awaitable[None]] = run_ffmpeg,
        ffprobe_runner: Callable[..., Awaitable[dict[str, Any]]] = run_ffprobe,
    ) -> None:
        self._s3 = s3_client
        self._store = store
        self._bucket = artifact_bucket
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._render_timeout_seconds = render_timeout_seconds
        self._max_download_bytes = max_download_bytes
        self._ffmpeg_runner = ffmpeg_runner
        self._ffprobe_runner = ffprobe_runner

    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> VideoArtifactReference:
        storyboard = version.storyboard
        if storyboard is None:
            raise ValueError("video rendering requires a storyboard")
        if len(version.image_artifacts) != 3:
            raise ValueError("video rendering requires exactly three image artifacts")
        if version.voice_artifact is None:
            raise ValueError("video rendering requires a voice artifact")

        fingerprint = _fingerprint(version, storyboard)
        reconciled = self._store.reconcile_video(version, fingerprint)
        if reconciled is not None:
            return self._reference(version, reconciled)

        await self._checkpoint(is_cancelled, "before_download")
        with tempfile.TemporaryDirectory(prefix="c8-render-") as tmp:
            tmp_path = Path(tmp)
            images_by_scene = {artifact.scene_number: artifact for artifact in version.image_artifacts}
            scenes = sorted(storyboard.scenes, key=lambda scene: scene.scene_number)

            image_paths: list[Path] = []
            for scene in scenes:
                artifact = images_by_scene.get(scene.scene_number)
                if artifact is None:
                    raise ValueError(f"no image artifact found for scene {scene.scene_number}")
                image_path = tmp_path / f"scene-{scene.scene_number}.jpg"
                self._download(_image_key(artifact), image_path)
                image_paths.append(image_path)
                await self._checkpoint(is_cancelled, f"after_download_scene_{scene.scene_number}")

            audio_path = tmp_path / "voiceover.mp3"
            self._download(_audio_key(version.voice_artifact), audio_path)
            await self._checkpoint(is_cancelled, "after_download_audio")

            await self._checkpoint(is_cancelled, "before_ffprobe_audio")
            audio_probe = await self._ffprobe_runner(
                self._ffprobe_path, str(audio_path), timeout_seconds=self._render_timeout_seconds
            )
            audio_duration = float(audio_probe["format"]["duration"])
            scene_durations = self._scaled_scene_durations(scenes, audio_duration)

            output_path = tmp_path / "final.mp4"
            args = build_render_args(
                scene_image_paths=image_paths,
                scene_durations=scene_durations,
                audio_path=audio_path,
                output_path=output_path,
                width=_TARGET_WIDTH,
                height=_TARGET_HEIGHT,
                fps=_TARGET_FPS,
            )

            await self._checkpoint(is_cancelled, "before_ffmpeg")
            await self._ffmpeg_runner(self._ffmpeg_path, args, timeout_seconds=self._render_timeout_seconds)

            await self._checkpoint(is_cancelled, "before_validation")
            output_probe = await self._ffprobe_runner(
                self._ffprobe_path, str(output_path), timeout_seconds=self._render_timeout_seconds
            )
            self._validate_output(output_probe, version)

            data = output_path.read_bytes()
            rendered = RenderedVideo(
                data=data,
                checksum_sha256=hashlib.sha256(data).hexdigest(),
                width=_TARGET_WIDTH,
                height=_TARGET_HEIGHT,
                duration_seconds=sum(scene_durations),
                video_codec="h264",
                audio_codec="aac",
                fps=_TARGET_FPS,
            )

            await self._checkpoint(is_cancelled, "before_s3_upload")
            metadata = {
                "campaign_id": str(version.campaign_id),
                "campaign_version": version.campaign_version,
                "render_fingerprint": fingerprint,
                "checksum_sha256": rendered.checksum_sha256,
                "width": rendered.width,
                "height": rendered.height,
                "duration_seconds": rendered.duration_seconds,
                "video_codec": rendered.video_codec,
                "audio_codec": rendered.audio_codec,
                "fps": rendered.fps,
                "renderer": "ffmpeg",
                "image_artifact_ids": [str(a.artifact_id) for a in version.image_artifacts],
                "voice_artifact_id": str(version.voice_artifact.artifact_id),
            }
            stored = self._store.put_video(version, rendered, metadata)
            return self._reference(version, stored)

    def _download(self, key: str, destination: Path) -> None:
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
        destination.write_bytes(data)

    @staticmethod
    def _scaled_scene_durations(scenes: list[Any], audio_duration: float) -> list[float]:
        total = sum(scene.duration_seconds for scene in scenes)
        scale = audio_duration / total
        if not (_MIN_SCALE <= scale <= _MAX_SCALE):
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED",
                "narration duration incompatible with storyboard timing",
                retryable=False,
            )
        return [scene.duration_seconds * scale for scene in scenes]

    @staticmethod
    def _validate_output(probe: dict[str, Any], version: CampaignVersion) -> None:
        streams = probe.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if len(video_streams) != 1 or len(audio_streams) != 1:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "rendered video is missing required streams", retryable=True
            )
        video = video_streams[0]
        if (
            video.get("codec_name") != "h264"
            or video.get("width") != _TARGET_WIDTH
            or video.get("height") != _TARGET_HEIGHT
        ):
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "rendered video codec or dimensions are incorrect", retryable=True
            )
        if audio_streams[0].get("codec_name") != "aac":
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "rendered video audio codec is incorrect", retryable=True
            )
        format_name = probe.get("format", {}).get("format_name", "")
        if "mp4" not in format_name:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "rendered output is not an MP4 container", retryable=True
            )
        try:
            duration = float(probe["format"]["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "rendered video duration is unreadable", retryable=True
            ) from exc
        constraints = version.constraints
        if not (constraints.min_duration_seconds <= duration <= constraints.max_duration_seconds):
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "rendered video duration is out of bounds", retryable=True
            )

    @staticmethod
    async def _checkpoint(is_cancelled: Callable[[], Awaitable[bool]], phase: str) -> None:
        if await is_cancelled():
            raise NodeCancelled(f"render_video:{phase}")

    @staticmethod
    def _reference(version: CampaignVersion, stored: StoredVideo) -> VideoArtifactReference:
        return VideoArtifactReference(
            artifact_id=stored.artifact_id,
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.VIDEO,
            mime_type="video/mp4",
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.checksum_sha256,
            created_at=stored.created_at,
            provider="ffmpeg",
        )
