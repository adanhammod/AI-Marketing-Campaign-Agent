import hashlib
import json
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid5

from campaign_contracts.artifacts import AudioArtifactReference, ImageArtifactReference, VideoArtifactReference
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, Storyboard
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.artifact_store import StoredVideo, VideoArtifactStore

from .audio_cue_library import build_sfx_cues
from .audio_mix import AudioMixRequest, SfxCue, build_audio_track
from .audio_plan import audio_plan_for
from .creative_plan_adapter import build_resolved_shots, build_text_cues, scale_resolved_shots
from .ffmpeg_renderer import FfmpegVideoRenderer
from .ffmpeg_runner import run_ffmpeg, run_ffprobe
from .models import LocalRenderRequest, ResolvedVideoShot, TextCue, VideoRenderer
from .music_resolver import ResolvedMusicAsset, resolve_music_asset
from .scene_mapping import resolve_scene_artifacts

_LOG = logging.getLogger(__name__)

_RENDER_SETTINGS_VERSION = 1
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


def _fingerprint(
    version: CampaignVersion,
    storyboard: Storyboard,
    *,
    music_checksum: str | None,
    sfx_checksums: list[tuple[str, str | None]],
) -> str:
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
        "video_style": version.brief.video_style.value,
        # Covers shot count/duration/camera/transition/text/audio_cues/visual_style
        # in one stroke -- final output now depends on all of these (Slices 3-4).
        "creative_video_plan": (
            version.creative_video_plan.model_dump(mode="json") if version.creative_video_plan is not None else None
        ),
        # Content checksums, not filesystem paths -- music/SFX are local, worker-
        # configured assets, not S3 artifacts with their own stable reference.
        "music_checksum": music_checksum,
        "sfx_checksums": sfx_checksums,
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
        renderer: VideoRenderer | None = None,
        audio_mixer: Callable[..., Awaitable[Path]] | None = None,
        music_path: Path | None = None,
        sfx_library_root: Path | None = None,
    ) -> None:
        self._s3 = s3_client
        self._store = store
        self._bucket = artifact_bucket
        self._ffprobe_path = ffprobe_path
        self._render_timeout_seconds = render_timeout_seconds
        self._max_download_bytes = max_download_bytes
        self._ffprobe_runner = ffprobe_runner
        self._music_path = music_path
        self._sfx_library_root = sfx_library_root
        if audio_mixer is not None:
            self._audio_mixer = audio_mixer
        else:
            # Bind to this pipeline's own ffmpeg_path/ffmpeg_runner (not a fresh real
            # ffmpeg call) so injecting a fake ffmpeg_runner for tests also covers the
            # audio-mix step, exactly like it already covers the video render step.
            async def _default_audio_mixer(request: AudioMixRequest, output_path: Path, *, timeout_seconds: float) -> Path:
                return await build_audio_track(
                    request,
                    output_path,
                    ffmpeg_path=ffmpeg_path,
                    timeout_seconds=timeout_seconds,
                    ffmpeg_runner=ffmpeg_runner,
                )

            self._audio_mixer = _default_audio_mixer
        self._renderer = renderer or FfmpegVideoRenderer(
            ffmpeg_path=ffmpeg_path,
            render_timeout_seconds=render_timeout_seconds,
            ffmpeg_runner=ffmpeg_runner,
        )

    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> VideoArtifactReference:
        storyboard = version.storyboard
        if storyboard is None:
            raise ValueError("video rendering requires a storyboard")
        if len(version.image_artifacts) != 3:
            raise ValueError("video rendering requires exactly three image artifacts")
        audio_plan = audio_plan_for(version.brief.video_style)
        if audio_plan.voiceover_required and version.voice_artifact is None:
            raise ValueError("video rendering requires a voice artifact")
        if audio_plan.music_required and self._music_path is None:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED",
                "video rendering requires a configured music_path for this video style",
                retryable=False,
            )

        # Music/SFX are resolved exactly once here (not re-resolved later) for two
        # reasons: (1) it lets the fingerprint capture their real content
        # checksums before deciding whether to reconcile, and (2) resolving SFX
        # assets only once means a missing optional cue is logged once, not once
        # per resolution pass. This is safe to do this early because, for every
        # style where music_required is True, voiceover_required is always False
        # (audio_plan_for never sets both), so resolved-shot timing never depends
        # on a real (download-then-ffprobe) audio duration in this branch.
        resolved_music: ResolvedMusicAsset | None = None
        early_resolved_shots: list[ResolvedVideoShot] = []
        early_sfx_cues: list[SfxCue] = []
        if audio_plan.music_required:
            try:
                resolved_music = resolve_music_asset(version, configured_path=self._music_path)
            except OSError as exc:
                raise WorkflowOperationError(
                    "ARTIFACT_VALIDATION_FAILED",
                    "configured music_path could not be read",
                    retryable=False,
                ) from exc
            plan = version.creative_video_plan
            if plan is not None:
                plan_shots = build_resolved_shots(plan)
                early_resolved_shots = scale_resolved_shots(
                    plan_shots, target_total_seconds=float(plan.total_duration_seconds)
                )
                early_sfx_cues = build_sfx_cues(early_resolved_shots, library_root=self._sfx_library_root)
            _LOG.info(
                "music_resolved",
                extra={
                    "campaign_id": str(version.campaign_id),
                    "source": resolved_music.source if resolved_music else None,
                    "mood": resolved_music.mood if resolved_music else None,
                    "sfx_cues_requested": sum(len(shot.audio_cues) for shot in early_resolved_shots),
                    "sfx_cues_resolved": len(early_sfx_cues),
                },
            )

        sfx_checksums = [
            (cue.path.name, hashlib.sha256(cue.path.read_bytes()).hexdigest()) for cue in early_sfx_cues
        ]
        fingerprint = _fingerprint(
            version,
            storyboard,
            music_checksum=resolved_music.checksum_sha256 if resolved_music else None,
            sfx_checksums=sfx_checksums,
        )
        reconciled = self._store.reconcile_video(version, fingerprint)
        if reconciled is not None:
            return self._reference(version, reconciled)

        await self._checkpoint(is_cancelled, "before_download")
        with tempfile.TemporaryDirectory(prefix="c8-render-") as tmp:
            tmp_path = Path(tmp)
            images_by_scene = resolve_scene_artifacts(version.image_artifacts)
            scenes = sorted(storyboard.scenes, key=lambda scene: scene.scene_number)

            scene_image_paths: dict[int, Path] = {}
            for scene_number, artifact in sorted(images_by_scene.items()):
                image_path = tmp_path / f"scene-{scene_number}.jpg"
                self._download(_image_key(artifact), image_path)
                scene_image_paths[scene_number] = image_path
                await self._checkpoint(is_cancelled, f"after_download_scene_{scene_number}")

            voiceover_path: Path | None = None
            if audio_plan.voiceover_required:
                voiceover_path = tmp_path / "voiceover.mp3"
                self._download(_audio_key(version.voice_artifact), voiceover_path)
                await self._checkpoint(is_cancelled, "after_download_audio")

                await self._checkpoint(is_cancelled, "before_ffprobe_audio")
                audio_probe = await self._ffprobe_runner(
                    self._ffprobe_path, str(voiceover_path), timeout_seconds=self._render_timeout_seconds
                )
                audio_duration = float(audio_probe["format"]["duration"])
            elif version.creative_video_plan is not None:
                # No voiceover to time against, but a CreativeVideoPlan exists --
                # its total_duration_seconds is the authoritative final-video
                # timeline for this style, so it drives both scene_durations
                # (via _scaled_scene_durations below) and the resolved shots
                # (via scale_resolved_shots below) off the same single clock.
                audio_duration = float(version.creative_video_plan.total_duration_seconds)
            else:
                # No voiceover and no plan -- fall back to the campaign's own target
                # duration, which the storyboard's total already equals by construction,
                # so this reuses _scaled_scene_durations unchanged with a scale of 1.0.
                audio_duration = float(version.constraints.target_duration_seconds)

            scene_durations = self._scaled_scene_durations(scenes, audio_duration, version.constraints)

            if audio_plan.music_required:
                # Computed once, up front (before the fingerprint/reconcile check) --
                # its scale target (plan.total_duration_seconds) is identical to the
                # audio_duration just computed above for this same branch, so reusing
                # it here introduces no drift and avoids a second SFX-resolution pass
                # (which would re-log every missing optional cue).
                resolved_shots = early_resolved_shots
                sfx_cues = early_sfx_cues
            else:
                resolved_shots = []
                if version.creative_video_plan is not None:
                    plan_shots = build_resolved_shots(version.creative_video_plan)
                    resolved_shots = scale_resolved_shots(plan_shots, target_total_seconds=audio_duration)
                sfx_cues = []
            text_cues: list[TextCue] = build_text_cues(resolved_shots) if resolved_shots else []

            await self._checkpoint(is_cancelled, "before_audio_mix")
            mixed_audio_path = tmp_path / "audio-mix.m4a"
            mix_request = AudioMixRequest(
                duration_seconds=audio_duration,
                music_path=resolved_music.path if resolved_music else None,
                voiceover_path=voiceover_path,
                sfx_cues=sfx_cues,
            )
            _LOG.info(
                "audio_mix_prepared",
                extra={
                    "campaign_id": str(version.campaign_id),
                    "duration_seconds": audio_duration,
                    "sfx_cues_used": len(sfx_cues),
                },
            )
            await self._audio_mixer(mix_request, mixed_audio_path, timeout_seconds=self._render_timeout_seconds)

            output_path = tmp_path / "final.mp4"
            render_request = LocalRenderRequest(
                scene_image_paths=scene_image_paths,
                scene_durations=scene_durations,
                audio_path=mixed_audio_path,
                storyboard=storyboard,
                headline=version.campaign_copy.headline if version.campaign_copy else "",
                key_message=version.strategy.key_message if version.strategy else "",
                cta=version.campaign_copy.call_to_action if version.campaign_copy else "",
                output_path=output_path,
                width=_TARGET_WIDTH,
                height=_TARGET_HEIGHT,
                fps=_TARGET_FPS,
                text_cues=text_cues,
                resolved_shots=resolved_shots,
            )

            await self._checkpoint(is_cancelled, "before_ffmpeg")
            rendered = await self._renderer.render(render_request)

            await self._checkpoint(is_cancelled, "before_validation")
            output_probe = await self._ffprobe_runner(
                self._ffprobe_path, str(output_path), timeout_seconds=self._render_timeout_seconds
            )
            self._validate_output(output_probe, version)

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
                "renderer": self._renderer.name,
                "image_artifact_ids": [str(a.artifact_id) for a in version.image_artifacts],
                "voice_artifact_id": str(version.voice_artifact.artifact_id) if version.voice_artifact else None,
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
    def _scaled_scene_durations(
        scenes: list[Any], audio_duration: float, constraints: CampaignConstraints
    ) -> dict[int, float]:
        # Bounds are derived from CampaignConstraints (the authoritative
        # product duration requirement, also enforced post-render by
        # _validate_output) rather than a fixed scale band, so narration
        # that would fail validation is rejected here -- before paying for
        # a full ffmpeg render -- instead of only being caught afterward.
        total = sum(scene.duration_seconds for scene in scenes)
        min_scale = constraints.min_duration_seconds / total
        max_scale = constraints.max_duration_seconds / total
        scale = audio_duration / total
        if not (min_scale <= scale <= max_scale):
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED",
                "narration duration incompatible with storyboard timing",
                retryable=False,
            )
        return {scene.scene_number: scene.duration_seconds * scale for scene in scenes}

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

    def _reference(self, version: CampaignVersion, stored: StoredVideo) -> VideoArtifactReference:
        return VideoArtifactReference(
            artifact_id=stored.artifact_id,
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.VIDEO,
            mime_type="video/mp4",
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.checksum_sha256,
            created_at=stored.created_at,
            provider=self._renderer.name,
        )
