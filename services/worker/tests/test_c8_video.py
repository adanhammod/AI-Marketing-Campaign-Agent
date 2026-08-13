import asyncio
import hashlib
import io
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.artifacts import AudioArtifactReference, ImageArtifactReference
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, Storyboard, StoryboardScene
from campaign_contracts.enums import CampaignStatus, WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.s3_artifact_store import S3ArtifactStore
from campaign_worker.video.compositor import build_render_args
from campaign_worker.video.ffmpeg_runner import run_ffmpeg, run_ffprobe
from campaign_worker.video.models import RenderedVideo
from campaign_worker.video.pipeline import FfmpegVideoPipeline, deterministic_video_artifact_id


def _version(*, language: str = "en-US") -> CampaignVersion:
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        tone="bright",
        language=language,
        target_audience="Urban professionals",
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
    image_artifacts = [
        ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=uuid4(),
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
    voice_artifact = AudioArtifactReference(
        artifact_id=uuid4(),
        campaign_id=uuid4(),
        campaign_version=2,
        workflow_step=WorkflowStep.VOICEOVER,
        mime_type="audio/mpeg",
        size_bytes=4096,
        checksum_sha256="b" * 64,
        created_at=now,
        provider="polly",
    )
    campaign_id = uuid4()
    image_artifacts = [artifact.model_copy(update={"campaign_id": campaign_id}) for artifact in image_artifacts]
    voice_artifact = voice_artifact.model_copy(update={"campaign_id": campaign_id})
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


def _mp4_bytes(payload: bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100) -> bytes:
    return payload


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


class _OutageS3:
    def put_object(self, **kwargs):
        raise RuntimeError("boom")

    def get_object(self, **kwargs):
        raise RuntimeError("boom")


def test_deterministic_video_artifact_id_is_stable_per_campaign_version():
    campaign_id = uuid4()
    first = deterministic_video_artifact_id(campaign_id, 2)
    second = deterministic_video_artifact_id(campaign_id, 2)
    third = deterministic_video_artifact_id(campaign_id, 3)
    assert first == second
    assert first != third


def _rendered_video(payload: bytes = _mp4_bytes()) -> RenderedVideo:
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


def test_s3_video_put_and_reconcile_round_trip():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    video = _rendered_video()
    fingerprint = "a" * 64
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": version.campaign_version,
        "render_fingerprint": fingerprint,
        "checksum_sha256": video.checksum_sha256,
        "width": video.width,
        "height": video.height,
        "duration_seconds": video.duration_seconds,
        "video_codec": video.video_codec,
        "audio_codec": video.audio_codec,
        "fps": video.fps,
        "renderer": "ffmpeg",
    }
    stored = store.put_video(version, video, metadata)
    assert stored.artifact_id == deterministic_video_artifact_id(version.campaign_id, version.campaign_version)
    assert f"campaigns/{version.campaign_id}/versions/2/video/final.mp4" in store._client.objects
    assert store.reconcile_video(version, fingerprint) == stored


def test_s3_video_reconcile_returns_none_when_object_absent():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    assert store.reconcile_video(version, "any-fingerprint") is None


def test_s3_video_reconcile_returns_none_on_fingerprint_mismatch():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    video = _rendered_video()
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": version.campaign_version,
        "render_fingerprint": "stale-fingerprint",
        "checksum_sha256": video.checksum_sha256,
    }
    store.put_video(version, video, metadata)
    assert store.reconcile_video(version, "new-fingerprint") is None


def test_s3_video_reconciliation_outage_is_not_treated_as_a_cache_miss():
    version = _version()
    store = S3ArtifactStore(_OutageS3(), "private-bucket")
    with pytest.raises(WorkflowOperationError) as error:
        store.reconcile_video(version, "fingerprint")
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_video_put_failure_maps_to_storage_unavailable():
    version = _version()
    store = S3ArtifactStore(_OutageS3(), "private-bucket")
    video = _rendered_video()
    with pytest.raises(WorkflowOperationError) as error:
        store.put_video(version, video, {"campaign_id": str(version.campaign_id)})
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_video_reconcile_non_404_client_error_maps_to_storage_unavailable():
    class _DeniedS3:
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    version = _version()
    store = S3ArtifactStore(_DeniedS3(), "private-bucket")
    with pytest.raises(WorkflowOperationError) as error:
        store.reconcile_video(version, "fingerprint")
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_video_reconcile_does_not_trust_corrupt_metadata():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/video/final"
    s3.objects[f"{prefix}.mp4"] = _mp4_bytes()
    s3.objects[f"{prefix}.metadata.json"] = b"{not-valid-json"

    assert store.reconcile_video(version, "any-fingerprint") is None


# ---------------------------------------------------------------------------
# video/compositor.py -- pure FFmpeg argument-graph builder (no subprocess)
# ---------------------------------------------------------------------------


def _scene_paths():
    return [f"/tmp/render/scene-{n}.jpg" for n in (1, 2, 3)]


def test_compositor_builds_a_flat_arg_list_not_a_shell_string():
    args = build_render_args(
        scene_image_paths=_scene_paths(),
        scene_durations=[5.0, 5.0, 5.0],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
    )
    assert isinstance(args, list)
    assert all(isinstance(item, str) for item in args)
    assert len(args) > 5
    non_filter_args = [item for item in args if item != args[args.index("-filter_complex") + 1]]
    assert not any(" && " in item or "|" in item for item in non_filter_args)


def test_compositor_uses_exactly_four_inputs_in_scene_order_then_audio():
    scene_paths = _scene_paths()
    args = build_render_args(
        scene_image_paths=scene_paths,
        scene_durations=[5.0, 5.0, 5.0],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
    )
    assert args.count("-i") == 4
    input_paths = [args[i + 1] for i, token in enumerate(args) if token == "-i"]
    assert input_paths == [*scene_paths, "/tmp/render/voiceover.mp3"]


def test_compositor_maps_concat_output_and_original_audio_stream():
    args = build_render_args(
        scene_image_paths=_scene_paths(),
        scene_durations=[5.0, 5.0, 5.0],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
    )
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "concat=n=3:v=1:a=0[outv]" in filter_complex
    assert args[args.index("-map") + 1] == "[outv]"
    map_indices = [i for i, token in enumerate(args) if token == "-map"]
    assert args[map_indices[1] + 1] == "3:a"


def test_compositor_scales_every_scene_to_target_resolution():
    args = build_render_args(
        scene_image_paths=_scene_paths(),
        scene_durations=[5.0, 5.0, 5.0],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
        width=1080,
        height=1920,
    )
    filter_complex = args[args.index("-filter_complex") + 1]
    assert filter_complex.count("scale=1080:1920") == 3


def test_compositor_applies_fade_in_only_on_first_scene_and_fade_out_only_on_last():
    args = build_render_args(
        scene_image_paths=_scene_paths(),
        scene_durations=[5.0, 5.0, 5.0],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
    )
    filter_complex = args[args.index("-filter_complex") + 1]
    chains = filter_complex.split(";")
    assert "fade=t=in" in chains[0]
    assert "fade=t=in" not in chains[1] and "fade=t=out" not in chains[0]
    assert "fade=t=out" in chains[2]


def test_compositor_encodes_h264_aac_yuv420p_with_configured_fps():
    args = build_render_args(
        scene_image_paths=_scene_paths(),
        scene_durations=[5.0, 5.0, 5.0],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
        fps=30,
    )
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-r") + 1] == "30"


def test_compositor_output_path_is_the_final_argument():
    args = build_render_args(
        scene_image_paths=_scene_paths(),
        scene_durations=[5.0, 5.0, 5.0],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
    )
    assert args[-1] == "/tmp/render/final.mp4"


def test_compositor_is_deterministic_for_identical_inputs():
    kwargs = dict(
        scene_image_paths=_scene_paths(),
        scene_durations=[4.5, 5.25, 5.25],
        audio_path="/tmp/render/voiceover.mp3",
        output_path="/tmp/render/final.mp4",
    )
    assert build_render_args(**kwargs) == build_render_args(**kwargs)


def test_compositor_rejects_mismatched_scene_and_duration_counts():
    with pytest.raises(ValueError):
        build_render_args(
            scene_image_paths=_scene_paths(),
            scene_durations=[5.0, 5.0],
            audio_path="/tmp/render/voiceover.mp3",
            output_path="/tmp/render/final.mp4",
        )


def test_compositor_rejects_empty_scenes():
    with pytest.raises(ValueError):
        build_render_args(
            scene_image_paths=[],
            scene_durations=[],
            audio_path="/tmp/render/voiceover.mp3",
            output_path="/tmp/render/final.mp4",
        )


# ---------------------------------------------------------------------------
# video/ffmpeg_runner.py -- real asyncio subprocess mechanics, using the
# Python interpreter itself as a stand-in "binary" so behavior (exit codes,
# timeouts, stderr capture) is exercised for real without needing ffmpeg.
# ---------------------------------------------------------------------------


def _fake_binary(tmp_path, script: str) -> str:
    path = tmp_path / "fake_binary.py"
    path.write_text(textwrap.dedent(script))
    return str(path)


@pytest.mark.asyncio
async def test_run_ffmpeg_missing_binary_raises_video_provider_unavailable():
    with pytest.raises(WorkflowOperationError) as error:
        await run_ffmpeg("/nonexistent/ffmpeg-binary-xyz", ["-version"], timeout_seconds=5)
    assert error.value.code == "VIDEO_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_run_ffmpeg_succeeds_on_zero_exit(tmp_path):
    script = _fake_binary(tmp_path, "import sys; sys.exit(0)")
    await run_ffmpeg(sys.executable, [script], timeout_seconds=5)


@pytest.mark.asyncio
async def test_run_ffmpeg_non_zero_exit_raises_video_provider_unavailable_with_sanitized_stderr(tmp_path):
    script = _fake_binary(
        tmp_path,
        """
        import sys
        sys.stderr.write("x" * 2000)
        sys.exit(1)
        """,
    )
    with pytest.raises(WorkflowOperationError) as error:
        await run_ffmpeg(sys.executable, [script], timeout_seconds=5)
    assert error.value.code == "VIDEO_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is True
    assert len(str(error.value)) < 1000


@pytest.mark.asyncio
async def test_run_ffmpeg_timeout_kills_the_process_and_raises_provider_timeout(tmp_path):
    script = _fake_binary(tmp_path, "import time; time.sleep(30)")
    with pytest.raises(WorkflowOperationError) as error:
        await run_ffmpeg(sys.executable, [script], timeout_seconds=0.2)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_run_ffprobe_missing_binary_raises_video_provider_unavailable():
    with pytest.raises(WorkflowOperationError) as error:
        await run_ffprobe("/nonexistent/ffprobe-binary-xyz", "/tmp/does-not-matter.mp4", timeout_seconds=5)
    assert error.value.code == "VIDEO_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_run_ffprobe_returns_parsed_json_on_success(tmp_path):
    script = _fake_binary(
        tmp_path,
        """
        import sys
        sys.stdout.write('{"streams": [{"codec_type": "video"}], "format": {"duration": "15.0"}}')
        sys.exit(0)
        """,
    )
    result = await run_ffprobe(sys.executable, str(tmp_path / "video.mp4"), timeout_seconds=5, extra_args=[script])
    assert result["streams"][0]["codec_type"] == "video"
    assert result["format"]["duration"] == "15.0"


@pytest.mark.asyncio
async def test_run_ffprobe_non_zero_exit_raises_invalid_provider_output(tmp_path):
    script = _fake_binary(tmp_path, "import sys; sys.exit(1)")
    with pytest.raises(WorkflowOperationError) as error:
        await run_ffprobe(sys.executable, str(tmp_path / "video.mp4"), timeout_seconds=5, extra_args=[script])
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_run_ffprobe_malformed_json_raises_invalid_provider_output(tmp_path):
    script = _fake_binary(
        tmp_path,
        """
        import sys
        sys.stdout.write('not-json')
        sys.exit(0)
        """,
    )
    with pytest.raises(WorkflowOperationError) as error:
        await run_ffprobe(sys.executable, str(tmp_path / "video.mp4"), timeout_seconds=5, extra_args=[script])
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_run_ffprobe_timeout_raises_provider_timeout(tmp_path):
    script = _fake_binary(tmp_path, "import time; time.sleep(30)")
    with pytest.raises(WorkflowOperationError) as error:
        await run_ffprobe(sys.executable, str(tmp_path / "video.mp4"), timeout_seconds=0.2, extra_args=[script])
    assert error.value.code == "PROVIDER_TIMEOUT"


# ---------------------------------------------------------------------------
# video/pipeline.py -- FfmpegVideoPipeline orchestration
# ---------------------------------------------------------------------------


def _image_key(artifact) -> str:
    prefix = f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}"
    return f"{prefix}/images/scene-{artifact.scene_number}.jpg"


def _audio_key(artifact) -> str:
    return f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}/audio/voiceover.mp3"


def _populated_s3(version: CampaignVersion) -> _S3:
    s3 = _S3()
    for artifact in version.image_artifacts:
        s3.objects[_image_key(artifact)] = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
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


class _RecordingRunners:
    def __init__(self, probes: dict | None = None, ffmpeg_error: Exception | None = None) -> None:
        self.ffmpeg_calls: list[list[str]] = []
        self.ffprobe_calls: list[str] = []
        self._probes = probes or {}
        self._ffmpeg_error = ffmpeg_error

    async def ffmpeg(self, ffmpeg_path, args, *, timeout_seconds):
        self.ffmpeg_calls.append(args)
        if self._ffmpeg_error:
            raise self._ffmpeg_error
        # Simulate ffmpeg actually producing the output file.
        await asyncio.to_thread(Path(args[-1]).write_bytes, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 500)

    async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
        self.ffprobe_calls.append(file_path)
        if str(file_path).endswith("voiceover.mp3"):
            return _VALID_AUDIO_PROBE
        return self._probes.get("output", _VALID_VIDEO_PROBE)


def _pipeline(s3=None, store=None, runners=None, **overrides) -> FfmpegVideoPipeline:
    runners = runners or _RecordingRunners()
    return FfmpegVideoPipeline(
        s3 or _S3(),
        store or S3ArtifactStore(s3 or _S3(), "private-bucket"),
        "private-bucket",
        ffmpeg_runner=runners.ffmpeg,
        ffprobe_runner=runners.ffprobe,
        render_timeout_seconds=overrides.get("render_timeout_seconds", 30),
    )


async def _never_cancelled() -> bool:
    return False


@pytest.mark.asyncio
async def test_pipeline_renders_and_stores_a_new_video():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    runners = _RecordingRunners()
    pipeline = _pipeline(s3, store, runners)

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.VIDEO
    assert artifact.mime_type == "video/mp4"
    assert artifact.provider == "ffmpeg"
    assert artifact.artifact_id == deterministic_video_artifact_id(version.campaign_id, version.campaign_version)
    assert len(runners.ffmpeg_calls) == 1
    # 3 images in scene order + audio, matching the compositor's input contract.
    render_args = runners.ffmpeg_calls[0]
    input_paths = [render_args[i + 1] for i, token in enumerate(render_args) if token == "-i"]
    assert [p.split("/")[-1] for p in input_paths] == ["scene-1.jpg", "scene-2.jpg", "scene-3.jpg", "voiceover.mp3"]


@pytest.mark.asyncio
async def test_pipeline_requires_prior_storyboard():
    version = _version().model_copy(update={"storyboard": None})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="storyboard"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_requires_exactly_three_image_artifacts():
    version = _version()
    version = version.model_copy(update={"image_artifacts": version.image_artifacts[:2]})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="image"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_requires_voice_artifact():
    version = _version().model_copy(update={"voice_artifact": None})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="voice"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_reconciles_existing_video_and_skips_ffmpeg():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    runners = _RecordingRunners()
    pipeline = _pipeline(s3, store, runners)

    first = await pipeline.acquire(version, _never_cancelled)
    assert len(runners.ffmpeg_calls) == 1
    second = await pipeline.acquire(version, _never_cancelled)
    assert len(runners.ffmpeg_calls) == 1
    assert second.artifact_id == first.artifact_id
    assert second.checksum_sha256 == first.checksum_sha256


@pytest.mark.asyncio
async def test_pipeline_scales_scene_durations_to_match_real_audio_duration():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    runners = _RecordingRunners()
    # Audio is 10s but storyboard totals 15s -- scale factor 10/15 ~= 0.667, inside no...
    # use a scale within the approved [0.85, 1.25] bound instead: 13.5 / 15 = 0.9
    runners._probes = {}

    class _ScaledRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            self.ffprobe_calls.append(file_path)
            if str(file_path).endswith("voiceover.mp3"):
                return {"format": {"duration": "13.5"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
            return _VALID_VIDEO_PROBE

    scaled_runners = _ScaledRunners()
    pipeline = _pipeline(s3, store, scaled_runners)

    await pipeline.acquire(version, _never_cancelled)

    render_args = scaled_runners.ffmpeg_calls[0]
    # Each of the 3 equal 5s scenes should scale by 13.5/15 = 0.9 -> 4.5s each.
    durations = [render_args[i + 1] for i, token in enumerate(render_args) if token == "-t"]
    assert durations == ["4.500", "4.500", "4.500"]


@pytest.mark.asyncio
async def test_pipeline_rejects_audio_duration_outside_scale_bound():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _OutOfBoundRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return {"format": {"duration": "3.0"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
            return _VALID_VIDEO_PROBE

    runners = _OutOfBoundRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert error.value.retryable is False
    assert runners.ffmpeg_calls == []


@pytest.mark.asyncio
async def test_pipeline_rejects_audio_duration_just_below_scale_bound():
    # Regression test for a real production incident: a 3x-repeated short
    # key_message narration produced 11.856s of Polly audio against a 15s
    # storyboard (scale = 11.856/15 = 0.7904), narrowly missing the 0.85
    # floor. The prior test suite only covered a comfortably in-bound scale
    # (0.9) and a grossly out-of-bound one (0.2); this locks in behavior at
    # the actual boundary that broke in production.
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _NearMissRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return {"format": {"duration": "11.856"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
            return _VALID_VIDEO_PROBE

    runners = _NearMissRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert str(error.value) == "narration duration incompatible with storyboard timing"
    assert error.value.retryable is False
    assert runners.ffmpeg_calls == []


@pytest.mark.asyncio
async def test_pipeline_rejects_duration_inside_old_fixed_band_but_below_constraints_minimum():
    # Regression test proving the pre-render check now derives its bounds
    # from CampaignConstraints (13-17s for this 15s storyboard, i.e. scale
    # [0.8667, 1.1333]) rather than the old fixed [0.85, 1.25] band. 12.9s
    # gives scale 0.86 -- the OLD floor (0.85) would have accepted this,
    # but it's below the real product minimum of 13s, so it must now be
    # rejected here rather than only being caught by _validate_output after
    # a full render.
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _JustBelowConstraintsMinRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return {"format": {"duration": "12.9"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
            return _VALID_VIDEO_PROBE

    runners = _JustBelowConstraintsMinRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert str(error.value) == "narration duration incompatible with storyboard timing"
    assert error.value.retryable is False
    assert runners.ffmpeg_calls == []


@pytest.mark.asyncio
async def test_pipeline_rejects_duration_inside_old_fixed_band_but_above_constraints_maximum():
    # Direct regression test for the real Luna production incident: the
    # retargeted narration fix produced an 18.696s Polly voiceover against
    # this 15s storyboard (scale 1.2464). The OLD fixed ceiling (1.25)
    # would have accepted this and gone on to render (and, separately,
    # time out) -- but 18.696s exceeds the real product maximum of 17s, so
    # it must now be rejected here, before any ffmpeg render is attempted.
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _JustAboveConstraintsMaxRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return {"format": {"duration": "18.696"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
            return _VALID_VIDEO_PROBE

    runners = _JustAboveConstraintsMaxRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert str(error.value) == "narration duration incompatible with storyboard timing"
    assert error.value.retryable is False
    assert runners.ffmpeg_calls == []


@pytest.mark.asyncio
async def test_pipeline_maps_ffprobe_output_validation_failures():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _BadDimensionsRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return _VALID_AUDIO_PROBE
            return {
                "format": {"duration": "15.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            }

    runners = _BadDimensionsRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_pipeline_maps_missing_audio_stream_in_output_as_invalid():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _NoAudioStreamRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return _VALID_AUDIO_PROBE
            return {
                "format": {"duration": "15.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920}],
            }

    runners = _NoAudioStreamRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_pipeline_rejects_wrong_audio_codec_in_output():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _WrongAudioCodecRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return _VALID_AUDIO_PROBE
            return {
                "format": {"duration": "15.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                    {"codec_type": "audio", "codec_name": "mp3"},
                ],
            }

    runners = _WrongAudioCodecRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_pipeline_rejects_non_mp4_container():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _WrongContainerRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return _VALID_AUDIO_PROBE
            return {
                "format": {"duration": "15.0", "format_name": "matroska,webm"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            }

    runners = _WrongContainerRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_pipeline_rejects_output_duration_outside_constraint_bounds():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")

    class _OutOfBoundsDurationRunners(_RecordingRunners):
        async def ffprobe(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
            if str(file_path).endswith("voiceover.mp3"):
                return _VALID_AUDIO_PROBE
            return {
                "format": {"duration": "45.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            }

    runners = _OutOfBoundsDurationRunners()
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_pipeline_rejects_empty_downloaded_input():
    version = _version()
    s3 = _populated_s3(version)
    image_key = _image_key(version.image_artifacts[0])
    s3.objects[image_key] = b""
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_pipeline_rejects_oversized_downloaded_input():
    version = _version()
    s3 = _populated_s3(version)
    image_key = _image_key(version.image_artifacts[0])
    s3.objects[image_key] = b"x" * 1000
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = FfmpegVideoPipeline(
        s3,
        store,
        "private-bucket",
        ffmpeg_runner=_RecordingRunners().ffmpeg,
        ffprobe_runner=_RecordingRunners().ffprobe,
        max_download_bytes=10,
    )

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_pipeline_propagates_ffmpeg_failure():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    runners = _RecordingRunners(
        ffmpeg_error=WorkflowOperationError("VIDEO_PROVIDER_UNAVAILABLE", "boom", retryable=True)
    )
    pipeline = _pipeline(s3, store, runners)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "VIDEO_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_pipeline_missing_s3_input_maps_to_storage_unavailable():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")  # empty -- nothing uploaded
    pipeline = _pipeline(_S3(), store)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "STORAGE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_download():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    async def cancel_first():
        return True

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_first)
    assert "before_download" in str(error.value)


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_ffmpeg():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    runners = _RecordingRunners()
    pipeline = _pipeline(s3, store, runners)
    calls = {"n": 0}

    async def cancel_before_ffmpeg():
        calls["n"] += 1
        # 1=before_download, then one per download (4), then before_ffprobe_audio, then before_ffmpeg
        return calls["n"] == 7

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_before_ffmpeg)
    assert "before_ffmpeg" in str(error.value)
    assert runners.ffmpeg_calls == []


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_s3_upload():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    runners = _RecordingRunners()
    pipeline = _pipeline(s3, store, runners)
    calls = {"n": 0}

    async def cancel_last():
        calls["n"] += 1
        return calls["n"] == 9

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_last)
    assert "before_s3_upload" in str(error.value)
    assert f"campaigns/{version.campaign_id}/versions/2/video/final.mp4" not in s3.objects
