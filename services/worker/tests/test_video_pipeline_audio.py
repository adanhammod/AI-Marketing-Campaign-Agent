import hashlib
import io
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.artifacts import AudioArtifactReference, ImageArtifactReference
from campaign_contracts.campaign import (
    CampaignConstraints,
    CampaignVersion,
    CreativeVideoPlan,
    RetryMetadata,
    Storyboard,
    StoryboardScene,
    VideoShot,
)
from campaign_contracts.enums import (
    AssetRole,
    AudioCueType,
    CameraMotion,
    CampaignStatus,
    ShotRole,
    TransitionType,
    VideoStyle,
    WorkflowStep,
)

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.storage.s3_artifact_store import S3ArtifactStore
from campaign_worker.video.audio_mix import AudioMixRequest
from campaign_worker.video.models import LocalRenderRequest, RenderedVideo
from campaign_worker.video.pipeline import FfmpegVideoPipeline


def _plan_shot(number, scene, duration, audio_cues=None) -> VideoShot:
    return VideoShot(
        shot_number=number,
        role=ShotRole.HOOK,
        source_scene_number=scene,
        asset_role=AssetRole.HERO_PRODUCT,
        visual_description="d",
        duration_seconds=duration,
        camera_motion=CameraMotion.STATIC,
        transition_in=TransitionType.CUT,
        audio_cues=audio_cues or [],
    )


def _plan() -> CreativeVideoPlan:
    shots = [
        _plan_shot(1, 1, 5.0),
        _plan_shot(2, 2, 5.0, [AudioCueType.TRANSITION_HIT]),
        _plan_shot(3, 3, 5.0, [AudioCueType.BRAND_HIT]),
    ]
    return CreativeVideoPlan(concept="c", visual_style="modern, energetic", total_duration_seconds=15, shots=shots)


def _version(*, video_style: VideoStyle = VideoStyle.CINEMATIC_TEXT_AD, with_plan: bool = True) -> CampaignVersion:
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals",
        video_style=video_style,
    )
    storyboard = Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration="Fresh coffee delivered weekly.",
                visual_prompt=f"artisan cold brew scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )
    campaign_id = uuid4()
    image_artifacts = [
        ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=2,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/jpeg",
            size_bytes=1024,
            checksum_sha256=str(n) * 64,
            created_at=now,
            provider="pexels",
            scene_number=n,
        )
        for n in (1, 2, 3)
    ]
    return CampaignVersion(
        campaign_id=campaign_id,
        campaign_version=2,
        parent_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        storyboard=storyboard,
        creative_video_plan=_plan() if with_plan else None,
        image_artifacts=image_artifacts,
        voice_artifact=None,
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def _image_key(artifact) -> str:
    prefix = f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}"
    return f"{prefix}/images/scene-{artifact.scene_number}.jpg"


def _populated_s3(version: CampaignVersion) -> _S3:
    s3 = _S3()
    for artifact in version.image_artifacts:
        s3.objects[_image_key(artifact)] = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    return s3


_VALID_VIDEO_PROBE = {
    "format": {"duration": "15.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "pix_fmt": "yuv420p"},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
}


class _RecordingRenderer:
    name = "recording-renderer"

    def __init__(self) -> None:
        self.requests: list[LocalRenderRequest] = []
        self.calls = 0

    async def render(self, request: LocalRenderRequest) -> RenderedVideo:
        self.calls += 1
        self.requests.append(request)
        payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
        request.output_path.write_bytes(payload)
        return RenderedVideo(
            data=payload,
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            width=1080,
            height=1920,
            duration_seconds=15.0,
            video_codec="h264",
            audio_codec="aac",
            fps=30,
        )


async def _fake_ffprobe(ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
    return _VALID_VIDEO_PROBE


class _RecordingAudioMixer:
    def __init__(self) -> None:
        self.requests: list[AudioMixRequest] = []

    async def __call__(self, request: AudioMixRequest, output_path: Path, *, timeout_seconds: float) -> Path:
        self.requests.append(request)
        output_path.write_bytes(b"fake-mixed-audio")
        return output_path


async def _fake_ffmpeg(ffmpeg_path, args, *, timeout_seconds):
    Path(args[-1]).write_bytes(b"fake")


async def _never_cancelled() -> bool:
    return False


def _pipeline(s3, store, *, renderer=None, mixer=None, music_path=None, sfx_library_root=None) -> FfmpegVideoPipeline:
    return FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=_fake_ffmpeg,
        ffprobe_runner=_fake_ffprobe,
        render_timeout_seconds=30,
        renderer=renderer or _RecordingRenderer(),
        audio_mixer=mixer or _RecordingAudioMixer(),
        music_path=music_path,
        sfx_library_root=sfx_library_root,
    )


def _write_sfx_files(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        (root / filename).write_bytes(content)


# ---------------------------------------------------------------------------
# Music required / missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cinematic_text_ad_requires_music_and_fails_clearly_when_missing():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store, music_path=None)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_cinematic_text_ad_fails_clearly_when_configured_music_path_is_unreadable(tmp_path):
    music_path = tmp_path / "does-not-exist.wav"  # configured but never written
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store, music_path=music_path)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_cinematic_text_ad_does_not_require_voiceover(tmp_path):
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    pipeline = _pipeline(s3, store, renderer=renderer, music_path=music_path)

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.VIDEO


# ---------------------------------------------------------------------------
# SFX cue building
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sfx_cues_are_built_from_plan_audio_cues_and_passed_to_mixer(tmp_path):
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    sfx_root = tmp_path / "sfx"
    _write_sfx_files(sfx_root, {"transition_hit.wav": b"hit-a", "brand_hit.wav": b"hit-b"})

    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    mixer = _RecordingAudioMixer()
    pipeline = _pipeline(s3, store, mixer=mixer, music_path=music_path, sfx_library_root=sfx_root)

    await pipeline.acquire(version, _never_cancelled)

    assert len(mixer.requests) == 1
    sfx_cues = mixer.requests[0].sfx_cues
    assert len(sfx_cues) == 2
    starts = sorted(cue.start_seconds for cue in sfx_cues)
    assert starts == pytest.approx([5.0, 10.0])


@pytest.mark.asyncio
async def test_missing_optional_sfx_asset_is_skipped_and_render_still_succeeds(tmp_path, caplog):
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    empty_sfx_root = tmp_path / "sfx-empty"
    empty_sfx_root.mkdir()

    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    mixer = _RecordingAudioMixer()
    pipeline = _pipeline(s3, store, mixer=mixer, music_path=music_path, sfx_library_root=empty_sfx_root)

    with caplog.at_level(logging.WARNING, logger="campaign_worker.video.audio_cue_library"):
        artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.VIDEO
    assert mixer.requests[0].sfx_cues == []
    assert len(caplog.records) == 2  # one warning per unresolved cue type


# ---------------------------------------------------------------------------
# VOICEOVER_AD / MUSIC_FIRST_REEL compatibility
# ---------------------------------------------------------------------------


def _voiceover_ad_version_with_voice_artifact() -> CampaignVersion:
    version = _version(video_style=VideoStyle.VOICEOVER_AD, with_plan=True)
    voice_artifact = AudioArtifactReference(
        artifact_id=uuid4(),
        campaign_id=version.campaign_id,
        campaign_version=2,
        workflow_step=WorkflowStep.VOICEOVER,
        mime_type="audio/mpeg",
        size_bytes=10,
        checksum_sha256="b" * 64,
        created_at=datetime.now(UTC),
        provider="polly",
    )
    return version.model_copy(update={"voice_artifact": voice_artifact})


async def _voiceover_ffprobe(ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
    if str(file_path).endswith("voiceover.mp3"):
        return {"format": {"duration": "15.0"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
    return _VALID_VIDEO_PROBE


@pytest.mark.asyncio
async def test_voiceover_ad_without_configured_music_never_receives_sfx_or_music():
    version = _voiceover_ad_version_with_voice_artifact()
    s3 = _populated_s3(version)
    s3.objects[f"campaigns/{version.campaign_id}/versions/2/audio/voiceover.mp3"] = b"fake-mp3"

    store = S3ArtifactStore(s3, "private-bucket")
    mixer = _RecordingAudioMixer()
    pipeline = FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=_fake_ffmpeg,
        ffprobe_runner=_voiceover_ffprobe,
        render_timeout_seconds=30,
        renderer=_RecordingRenderer(),
        audio_mixer=mixer,
        music_path=None,
        sfx_library_root=None,
    )

    artifact = await pipeline.acquire(version, _never_cancelled)

    # Graceful degradation: no music configured, render still succeeds voiceover-only.
    assert artifact.workflow_step == WorkflowStep.VIDEO
    assert mixer.requests[0].music_path is None
    assert mixer.requests[0].sfx_cues == []
    assert mixer.requests[0].voiceover_path is not None


@pytest.mark.asyncio
async def test_voiceover_ad_with_configured_music_mixes_voiceover_and_music(tmp_path):
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    version = _voiceover_ad_version_with_voice_artifact()
    s3 = _populated_s3(version)
    s3.objects[f"campaigns/{version.campaign_id}/versions/2/audio/voiceover.mp3"] = b"fake-mp3"

    store = S3ArtifactStore(s3, "private-bucket")
    mixer = _RecordingAudioMixer()
    pipeline = FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=_fake_ffmpeg,
        ffprobe_runner=_voiceover_ffprobe,
        render_timeout_seconds=30,
        renderer=_RecordingRenderer(),
        audio_mixer=mixer,
        music_path=music_path,
        sfx_library_root=None,
    )

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.VIDEO
    assert mixer.requests[0].voiceover_path is not None
    assert mixer.requests[0].music_path is not None


@pytest.mark.asyncio
async def test_voiceover_ad_with_unreadable_configured_music_still_succeeds_voiceover_only(tmp_path, caplog):
    music_path = tmp_path / "does-not-exist.wav"  # configured but never written
    version = _voiceover_ad_version_with_voice_artifact()
    s3 = _populated_s3(version)
    s3.objects[f"campaigns/{version.campaign_id}/versions/2/audio/voiceover.mp3"] = b"fake-mp3"

    store = S3ArtifactStore(s3, "private-bucket")
    mixer = _RecordingAudioMixer()
    pipeline = FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=_fake_ffmpeg,
        ffprobe_runner=_voiceover_ffprobe,
        render_timeout_seconds=30,
        renderer=_RecordingRenderer(),
        audio_mixer=mixer,
        music_path=music_path,
        sfx_library_root=None,
    )

    with caplog.at_level(logging.WARNING, logger="campaign_worker.video.pipeline"):
        artifact = await pipeline.acquire(version, _never_cancelled)

    # Optional background music being unreadable must never fail the campaign.
    assert artifact.workflow_step == WorkflowStep.VIDEO
    assert mixer.requests[0].voiceover_path is not None
    assert mixer.requests[0].music_path is None
    assert any("optional_music_unavailable" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_music_first_reel_remains_unimplemented():
    version = _version(video_style=VideoStyle.MUSIC_FIRST_REEL)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    with pytest.raises(NotImplementedError):
        await pipeline.acquire(version, _never_cancelled)


# ---------------------------------------------------------------------------
# Fingerprint / idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_music_and_sfx_reconciles_without_rerendering(tmp_path):
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music-v1")
    sfx_root = tmp_path / "sfx"
    _write_sfx_files(sfx_root, {"transition_hit.wav": b"hit-a", "brand_hit.wav": b"hit-b"})

    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    pipeline = _pipeline(s3, store, renderer=renderer, music_path=music_path, sfx_library_root=sfx_root)

    await pipeline.acquire(version, _never_cancelled)
    assert renderer.calls == 1

    await pipeline.acquire(version, _never_cancelled)
    assert renderer.calls == 1  # reconciled from cache -- no second render


@pytest.mark.asyncio
async def test_changed_music_content_invalidates_the_cached_video(tmp_path):
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music-v1")
    sfx_root = tmp_path / "sfx"
    _write_sfx_files(sfx_root, {"transition_hit.wav": b"hit-a", "brand_hit.wav": b"hit-b"})

    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    pipeline = _pipeline(s3, store, renderer=renderer, music_path=music_path, sfx_library_root=sfx_root)
    await pipeline.acquire(version, _never_cancelled)
    assert renderer.calls == 1

    music_path.write_bytes(b"fake-music-v2-totally-different")
    await pipeline.acquire(version, _never_cancelled)
    assert renderer.calls == 2  # music content changed -> cache must miss


@pytest.mark.asyncio
async def test_changed_sfx_content_invalidates_the_cached_video(tmp_path):
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    sfx_root = tmp_path / "sfx"
    _write_sfx_files(sfx_root, {"transition_hit.wav": b"hit-a", "brand_hit.wav": b"hit-b"})

    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    pipeline = _pipeline(s3, store, renderer=renderer, music_path=music_path, sfx_library_root=sfx_root)
    await pipeline.acquire(version, _never_cancelled)
    assert renderer.calls == 1

    (sfx_root / "transition_hit.wav").write_bytes(b"hit-a-totally-different-now")
    await pipeline.acquire(version, _never_cancelled)
    assert renderer.calls == 2  # sfx content changed -> cache must miss
