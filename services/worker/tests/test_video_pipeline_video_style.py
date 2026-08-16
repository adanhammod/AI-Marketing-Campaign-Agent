import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.artifacts import AudioArtifactReference, ImageArtifactReference
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, Storyboard, StoryboardScene
from campaign_contracts.enums import CampaignStatus, VideoStyle, WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.storage.s3_artifact_store import S3ArtifactStore
from campaign_worker.video.audio_mix import AudioMixRequest
from campaign_worker.video.pipeline import FfmpegVideoPipeline


def _version(*, video_style: VideoStyle = VideoStyle.VOICEOVER_AD, with_voice: bool = True) -> CampaignVersion:
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


_VALID_AUDIO_PROBE = {"format": {"duration": "15.0"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
_VALID_VIDEO_PROBE = {
    "format": {"duration": "15.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "pix_fmt": "yuv420p"},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
}


class _RecordingFfmpeg:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, ffmpeg_path, args, *, timeout_seconds):
        self.calls.append(args)
        Path(args[-1]).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 500)


class _RecordingFfprobe:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
        self.calls.append(str(file_path))
        if str(file_path).endswith("voiceover.mp3"):
            return _VALID_AUDIO_PROBE
        return _VALID_VIDEO_PROBE


class _RecordingAudioMixer:
    def __init__(self) -> None:
        self.calls: list[AudioMixRequest] = []

    async def __call__(self, request: AudioMixRequest, output_path: Path, *, timeout_seconds: float) -> Path:
        self.calls.append(request)
        output_path.write_bytes(b"fake-mixed-audio")
        return output_path


def _pipeline(s3, store, *, ffmpeg=None, ffprobe=None, audio_mixer=None, music_path=None) -> FfmpegVideoPipeline:
    return FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=ffmpeg or _RecordingFfmpeg(),
        ffprobe_runner=ffprobe or _RecordingFfprobe(),
        render_timeout_seconds=30,
        audio_mixer=audio_mixer or _RecordingAudioMixer(),
        music_path=music_path,
    )


async def _never_cancelled() -> bool:
    return False


@pytest.mark.asyncio
async def test_cinematic_text_ad_does_not_require_a_voice_artifact(tmp_path):
    version = _version(video_style=VideoStyle.CINEMATIC_TEXT_AD, with_voice=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    pipeline = _pipeline(s3, store, music_path=music_path)

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.VIDEO


@pytest.mark.asyncio
async def test_voiceover_ad_still_requires_a_voice_artifact():
    version = _version(video_style=VideoStyle.VOICEOVER_AD, with_voice=False)
    pipeline = _pipeline(_S3(), S3ArtifactStore(_S3(), "private-bucket"))
    with pytest.raises(ValueError, match="voice artifact"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_cinematic_text_ad_raises_clearly_when_music_path_not_configured():
    version = _version(video_style=VideoStyle.CINEMATIC_TEXT_AD, with_voice=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store, music_path=None)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_audio_mixer_receives_voiceover_path_for_voiceover_ad():
    version = _version(video_style=VideoStyle.VOICEOVER_AD, with_voice=True)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    mixer = _RecordingAudioMixer()
    pipeline = _pipeline(s3, store, audio_mixer=mixer)

    await pipeline.acquire(version, _never_cancelled)

    assert len(mixer.calls) == 1
    request = mixer.calls[0]
    assert request.voiceover_path is not None
    assert request.voiceover_path.name == "voiceover.mp3"
    assert request.music_path is None
    assert request.duration_seconds == 15.0


@pytest.mark.asyncio
async def test_audio_mixer_receives_music_path_for_cinematic_text_ad(tmp_path):
    version = _version(video_style=VideoStyle.CINEMATIC_TEXT_AD, with_voice=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    mixer = _RecordingAudioMixer()
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    pipeline = _pipeline(s3, store, audio_mixer=mixer, music_path=music_path)

    await pipeline.acquire(version, _never_cancelled)

    assert len(mixer.calls) == 1
    request = mixer.calls[0]
    assert request.music_path == music_path
    assert request.voiceover_path is None


@pytest.mark.asyncio
async def test_cinematic_text_ad_uses_target_duration_seconds_not_probed_audio(tmp_path):
    # No voiceover to ffprobe -- duration must come from constraints.target_duration_seconds
    # (15), which equals the storyboard's own total, so scale is 1.0 and scenes stay 5s each.
    version = _version(video_style=VideoStyle.CINEMATIC_TEXT_AD, with_voice=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    ffmpeg = _RecordingFfmpeg()
    mixer = _RecordingAudioMixer()
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    pipeline = _pipeline(s3, store, ffmpeg=ffmpeg, audio_mixer=mixer, music_path=music_path)

    await pipeline.acquire(version, _never_cancelled)

    assert mixer.calls[0].duration_seconds == 15.0
    render_args = ffmpeg.calls[0]
    durations = [render_args[i + 1] for i, token in enumerate(render_args) if token == "-t"]
    assert durations == ["5.000", "5.000", "5.000"]


@pytest.mark.asyncio
async def test_cinematic_text_ad_never_ffprobes_a_voiceover_file(tmp_path):
    version = _version(video_style=VideoStyle.CINEMATIC_TEXT_AD, with_voice=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    ffprobe = _RecordingFfprobe()
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    pipeline = _pipeline(s3, store, ffprobe=ffprobe, music_path=music_path)

    await pipeline.acquire(version, _never_cancelled)

    assert not any(call.endswith("voiceover.mp3") for call in ffprobe.calls)


@pytest.mark.asyncio
async def test_metadata_voice_artifact_id_is_none_for_cinematic_text_ad(tmp_path):
    version = _version(video_style=VideoStyle.CINEMATIC_TEXT_AD, with_voice=False)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    pipeline = _pipeline(s3, store, music_path=music_path)

    await pipeline.acquire(version, _never_cancelled)

    metadata_key = f"campaigns/{version.campaign_id}/versions/2/video/final.metadata.json"
    metadata = json.loads(s3.objects[metadata_key])
    assert metadata["voice_artifact_id"] is None


@pytest.mark.asyncio
async def test_fingerprint_differs_by_video_style_so_reconciliation_does_not_cross_styles(tmp_path):
    version = _version(video_style=VideoStyle.VOICEOVER_AD, with_voice=True)
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)
    await pipeline.acquire(version, _never_cancelled)  # renders + stores under VOICEOVER_AD's fingerprint

    cinematic_version = version.model_copy(
        update={
            "voice_artifact": None,
            "brief": version.brief.model_copy(update={"video_style": VideoStyle.CINEMATIC_TEXT_AD}),
        }
    )
    ffmpeg = _RecordingFfmpeg()
    mixer = _RecordingAudioMixer()
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"fake-music")
    pipeline2 = _pipeline(s3, store, ffmpeg=ffmpeg, audio_mixer=mixer, music_path=music_path)

    await pipeline2.acquire(cinematic_version, _never_cancelled)

    # A real second render happened -- the CINEMATIC_TEXT_AD fingerprint did not
    # incorrectly reconcile against the VOICEOVER_AD version's cached artifact.
    assert len(ffmpeg.calls) == 1
    assert len(mixer.calls) == 1
