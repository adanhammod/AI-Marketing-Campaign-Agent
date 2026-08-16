import hashlib
import io
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
from campaign_contracts.enums import AssetRole, CameraMotion, CampaignStatus, ShotRole, TransitionType, VideoStyle, WorkflowStep

from campaign_worker.storage.s3_artifact_store import S3ArtifactStore
from campaign_worker.video.models import LocalRenderRequest, RenderedVideo
from campaign_worker.video.pipeline import FfmpegVideoPipeline


def _plan(total_duration_seconds: int = 15) -> CreativeVideoPlan:
    shots = [
        VideoShot(
            shot_number=1,
            role=ShotRole.HOOK,
            source_scene_number=1,
            asset_role=AssetRole.HERO_PRODUCT,
            visual_description="Hook shot",
            duration_seconds=total_duration_seconds * 0.4,
            text="HOOK LINE.",
            camera_motion=CameraMotion.PUSH_IN,
            transition_in=TransitionType.CUT,
        ),
        VideoShot(
            shot_number=2,
            role=ShotRole.MESSAGE,
            source_scene_number=2,
            asset_role=AssetRole.LIFESTYLE_PRODUCT,
            visual_description="Message shot",
            duration_seconds=total_duration_seconds * 0.35,
            text=None,
            camera_motion=CameraMotion.PAN_LEFT,
            transition_in=TransitionType.MASK_REVEAL,
        ),
        VideoShot(
            shot_number=3,
            role=ShotRole.CTA,
            source_scene_number=3,
            asset_role=AssetRole.CTA_FRAME,
            visual_description="CTA shot",
            duration_seconds=total_duration_seconds * 0.25,
            text="CTA LINE.",
            camera_motion=CameraMotion.STATIC,
            transition_in=TransitionType.CROSSFADE,
        ),
    ]
    return CreativeVideoPlan(
        concept="Test concept",
        visual_style="modern, premium",
        total_duration_seconds=total_duration_seconds,
        shots=shots,
    )


def _version(*, video_style: VideoStyle = VideoStyle.VOICEOVER_AD, with_voice: bool = True, with_plan: bool = True) -> CampaignVersion:
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
    voice_artifact = None
    if with_voice:
        voice_artifact = AudioArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=2,
            workflow_step=WorkflowStep.VOICEOVER,
            mime_type="audio/mpeg",
            size_bytes=4096,
            checksum_sha256="b" * 64,
            created_at=now,
            provider="polly",
        )
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
        voice_artifact=voice_artifact,
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


def _audio_key(artifact) -> str:
    return f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}/audio/voiceover.mp3"


def _populated_s3(version: CampaignVersion) -> _S3:
    s3 = _S3()
    for artifact in version.image_artifacts:
        s3.objects[_image_key(artifact)] = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    if version.voice_artifact is not None:
        s3.objects[_audio_key(version.voice_artifact)] = b"fake-mp3-bytes"
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

    async def render(self, request: LocalRenderRequest) -> RenderedVideo:
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


class _RecordingFfprobe:
    def __init__(self, voiceover_duration: str = "15.0") -> None:
        self._voiceover_duration = voiceover_duration

    async def __call__(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
        if str(file_path).endswith("voiceover.mp3"):
            return {"format": {"duration": self._voiceover_duration}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
        return _VALID_VIDEO_PROBE


async def _fake_ffmpeg(ffmpeg_path, args, *, timeout_seconds):
    Path(args[-1]).write_bytes(b"fake")


async def _fake_audio_mixer(request, output_path, *, timeout_seconds):
    output_path.write_bytes(b"fake-mixed-audio")
    return output_path


async def _never_cancelled() -> bool:
    return False


def _pipeline(s3, store, *, renderer=None, ffprobe=None) -> FfmpegVideoPipeline:
    return FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=_fake_ffmpeg,
        ffprobe_runner=ffprobe or _RecordingFfprobe(),
        render_timeout_seconds=30,
        renderer=renderer,
        audio_mixer=_fake_audio_mixer,
    )


@pytest.mark.asyncio
async def test_pipeline_passes_resolved_shots_when_plan_present():
    version = _version(with_plan=True)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    pipeline = _pipeline(s3, store, renderer=renderer)

    await pipeline.acquire(version, _never_cancelled)

    assert len(renderer.requests) == 1
    request = renderer.requests[0]
    assert len(request.resolved_shots) == 3
    assert [s.shot_number for s in request.resolved_shots] == [1, 2, 3]
    assert request.text_cues != []


@pytest.mark.asyncio
async def test_pipeline_resolved_shots_empty_when_no_plan():
    version = _version(with_plan=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    pipeline = _pipeline(s3, store, renderer=renderer)

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.VIDEO
    assert len(renderer.requests) == 1
    assert renderer.requests[0].resolved_shots == []
    assert renderer.requests[0].text_cues == []


@pytest.mark.asyncio
async def test_pipeline_scales_resolved_shots_to_real_voiceover_duration_for_voiceover_ad():
    # Plan totals 15s but real Polly audio is 13.5s (within the approved scale
    # bound for a 15s storyboard) -- resolved shots must scale to match, the
    # same minimal behavior-preserving transformation _scaled_scene_durations
    # already applies to scene durations.
    version = _version(video_style=VideoStyle.VOICEOVER_AD, with_voice=True, with_plan=True)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    pipeline = _pipeline(s3, store, renderer=renderer, ffprobe=_RecordingFfprobe(voiceover_duration="13.5"))

    await pipeline.acquire(version, _never_cancelled)

    request = renderer.requests[0]
    total = sum(shot.duration_seconds for shot in request.resolved_shots)
    assert total == pytest.approx(13.5, abs=0.01)


@pytest.mark.asyncio
async def test_pipeline_uses_plan_total_duration_for_cinematic_text_ad_not_constraints_target(tmp_path):
    # Plan is intentionally shorter (12s) than constraints.target_duration_seconds
    # (15s) but still within [min,max] bounds -- CreativeVideoPlan.total_duration_seconds
    # must drive the timeline, not the campaign's generic target duration.
    version = _version(video_style=VideoStyle.CINEMATIC_TEXT_AD, with_voice=False, with_plan=True)
    version = version.model_copy(update={"creative_video_plan": _plan(total_duration_seconds=14)})
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    renderer = _RecordingRenderer()
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    pipeline = FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=_fake_ffmpeg,
        ffprobe_runner=_RecordingFfprobe(),
        render_timeout_seconds=30,
        renderer=renderer,
        audio_mixer=_fake_audio_mixer,
        music_path=music_path,
    )

    await pipeline.acquire(version, _never_cancelled)

    request = renderer.requests[0]
    total = sum(shot.duration_seconds for shot in request.resolved_shots)
    assert total == pytest.approx(14.0, abs=0.01)


@pytest.mark.asyncio
async def test_historical_campaign_without_plan_still_renders_successfully():
    version = _version(with_plan=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store, renderer=_RecordingRenderer())

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.VIDEO
