import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, Storyboard, StoryboardScene
from campaign_contracts.enums import CampaignStatus, WorkflowStep

from campaign_worker.audio.normalizer import AudioNormalizer
from campaign_worker.audio.pipeline import PollyVoicePipeline, deterministic_voice_artifact_id
from campaign_worker.audio.processor import AudioProcessor
from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.s3_artifact_store import S3ArtifactStore


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
    return CampaignVersion(
        campaign_id=uuid4(),
        campaign_version=2,
        parent_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        storyboard=storyboard,
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )


def _mp3_bytes(payload: bytes = b"\xff\xfb\x90\x00" + b"\x00" * 100) -> bytes:
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


def test_audio_processor_computes_checksum_for_valid_mp3():
    processor = AudioProcessor()
    normalized = processor.validate(_mp3_bytes())
    assert normalized.checksum_sha256 == hashlib.sha256(_mp3_bytes()).hexdigest()
    assert normalized.data == _mp3_bytes()


def test_audio_processor_accepts_id3_tagged_mp3():
    processor = AudioProcessor()
    normalized = processor.validate(b"ID3" + b"\x00" * 50)
    assert normalized.data.startswith(b"ID3")


def test_audio_processor_rejects_empty_output():
    with pytest.raises(WorkflowOperationError) as error:
        AudioProcessor().validate(b"")
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


def test_audio_processor_rejects_non_mp3_bytes():
    with pytest.raises(WorkflowOperationError) as error:
        AudioProcessor().validate(b"not-audio-data-at-all")
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


def test_audio_processor_rejects_oversized_output():
    with pytest.raises(WorkflowOperationError) as error:
        AudioProcessor(max_bytes=3).validate(_mp3_bytes())
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


def test_deterministic_voice_artifact_id_is_stable_per_campaign_version():
    campaign_id = uuid4()
    first = deterministic_voice_artifact_id(campaign_id, 2)
    second = deterministic_voice_artifact_id(campaign_id, 2)
    third = deterministic_voice_artifact_id(campaign_id, 3)
    assert first == second
    assert first != third


def test_s3_audio_put_and_reconcile_round_trip():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    audio = AudioProcessor().validate(_mp3_bytes())
    fingerprint = hashlib.sha256(b"Fresh coffee delivered weekly.").hexdigest()
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": version.campaign_version,
        "narration_fingerprint": fingerprint,
        "polly_voice_id": "Joanna",
        "polly_engine": "neural",
        "language_code": "en-US",
        "checksum_sha256": audio.checksum_sha256,
    }
    stored = store.put_audio(version, audio, metadata)
    assert stored.artifact_id == deterministic_voice_artifact_id(version.campaign_id, version.campaign_version)
    assert f"campaigns/{version.campaign_id}/versions/2/audio/voiceover.mp3" in store._client.objects
    assert store.reconcile_audio(version, fingerprint) == stored


def test_s3_audio_reconcile_returns_none_when_object_absent():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    assert store.reconcile_audio(version, "any-fingerprint") is None


def test_s3_audio_reconcile_returns_none_on_fingerprint_mismatch():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    audio = AudioProcessor().validate(_mp3_bytes())
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": version.campaign_version,
        "narration_fingerprint": "stale-fingerprint",
        "polly_voice_id": "Joanna",
        "polly_engine": "neural",
        "language_code": "en-US",
        "checksum_sha256": audio.checksum_sha256,
    }
    store.put_audio(version, audio, metadata)
    assert store.reconcile_audio(version, "new-fingerprint") is None


def test_s3_audio_reconciliation_outage_is_not_treated_as_a_cache_miss():
    version = _version()
    store = S3ArtifactStore(_OutageS3(), "private-bucket")
    with pytest.raises(WorkflowOperationError) as error:
        store.reconcile_audio(version, "fingerprint")
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_audio_put_failure_maps_to_storage_unavailable():
    version = _version()
    store = S3ArtifactStore(_OutageS3(), "private-bucket")
    audio = AudioProcessor().validate(_mp3_bytes())
    with pytest.raises(WorkflowOperationError) as error:
        store.put_audio(version, audio, {"campaign_id": str(version.campaign_id)})
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_audio_reconcile_non_404_client_error_maps_to_storage_unavailable():
    class _DeniedS3:
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    version = _version()
    store = S3ArtifactStore(_DeniedS3(), "private-bucket")
    with pytest.raises(WorkflowOperationError) as error:
        store.reconcile_audio(version, "fingerprint")
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_audio_reconcile_does_not_trust_corrupt_metadata():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/audio/voiceover"
    s3.objects[f"{prefix}.mp3"] = _mp3_bytes()
    s3.objects[f"{prefix}.metadata.json"] = b"{not-valid-json"

    assert store.reconcile_audio(version, "any-fingerprint") is None


async def _never_cancelled() -> bool:
    return False


class _Polly:
    def __init__(self, audio: bytes = _mp3_bytes(), error: Exception | None = None) -> None:
        self.audio = audio
        self.error = error
        self.calls: list[dict] = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"AudioStream": io.BytesIO(self.audio)}


_LOUDNORM_STDERR = """
{
\t"input_i" : "-24.53",
\t"input_tp" : "-7.97",
\t"input_lra" : "0.90",
\t"input_thresh" : "-34.78",
\t"output_i" : "-16.20",
\t"output_tp" : "-1.50",
\t"output_lra" : "1.20",
\t"output_thresh" : "-26.44",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.20"
}
"""


class _PassthroughNormalizerRunner:
    """Fake AudioNormalizer ffmpeg_runner: echoes input bytes through unchanged,
    with canned loudnorm stats -- keeps PollyVoicePipeline tests independent
    of a real ffmpeg binary, matching test_audio_normalizer.py's conventions.
    """

    async def __call__(self, ffmpeg_path, args, *, timeout_seconds, unavailable_code="VIDEO_PROVIDER_UNAVAILABLE"):
        input_path = args[args.index("-i") + 1]
        output_path = args[-1]

        def _copy() -> None:
            Path(output_path).write_bytes(Path(input_path).read_bytes())

        await asyncio.to_thread(_copy)
        return _LOUDNORM_STDERR


def _normalizer() -> AudioNormalizer:
    return AudioNormalizer(ffmpeg_runner=_PassthroughNormalizerRunner())


class _FakeDurationFfprobeRunner:
    """Fake ffprobe_runner for PollyVoicePipeline's post-synthesis duration
    check: returns canned durations in sequence (first call probes the raw
    synthesized/loudness-normalized audio; a second call, only made if a
    tempo correction ran, probes the adjusted output). The last value is
    reused for any further calls. Defaults to a comfortably in-range
    duration, so tests that don't care about timing behave exactly as
    before this feature existed."""

    def __init__(self, durations: list[float] | None = None) -> None:
        self._durations = list(durations) if durations is not None else [15.0]
        self.calls: list[str] = []

    async def __call__(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
        self.calls.append(str(file_path))
        duration = self._durations.pop(0) if len(self._durations) > 1 else self._durations[0]
        return {"format": {"duration": str(duration)}, "streams": [{"codec_type": "audio", "codec_name": "mp3"}]}


class _FakeTempoFfmpegRunner:
    """Fake ffmpeg_runner for the tempo-correction path: echoes input bytes
    through unchanged (content isn't asserted by these tests -- the paired
    _FakeDurationFfprobeRunner's second canned value is what represents the
    "real" post-correction duration)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, ffmpeg_path, args, *, timeout_seconds, unavailable_code="VIDEO_PROVIDER_UNAVAILABLE"):
        self.calls.append(args)
        input_path = args[args.index("-i") + 1]
        output_path = args[-1]
        Path(output_path).write_bytes(Path(input_path).read_bytes())


def _pipeline(
    polly=None, store=None, normalizer=None, ffprobe_runner=None, ffmpeg_runner=None, **overrides
) -> PollyVoicePipeline:
    return PollyVoicePipeline(
        polly or _Polly(),
        store or S3ArtifactStore(_S3(), "private-bucket"),
        AudioProcessor(),
        normalizer or _normalizer(),
        voice_id=overrides.get("voice_id"),
        engine=overrides.get("engine", "neural"),
        ffprobe_runner=ffprobe_runner or _FakeDurationFfprobeRunner(),
        ffmpeg_runner=ffmpeg_runner or _FakeTempoFfmpegRunner(),
    )


@pytest.mark.asyncio
async def test_pipeline_synthesizes_and_stores_new_voiceover():
    version = _version()
    polly = _Polly()
    pipeline = _pipeline(polly)
    artifact = await pipeline.acquire(version, _never_cancelled)
    assert artifact.workflow_step == WorkflowStep.VOICEOVER
    assert artifact.mime_type == "audio/mpeg"
    assert artifact.provider == "polly"
    assert artifact.artifact_id == deterministic_voice_artifact_id(version.campaign_id, version.campaign_version)
    call = polly.calls[0]
    assert call["Text"] == (
        '<speak>Fresh coffee delivered weekly.<break time="350ms"/>'
        'Fresh coffee delivered weekly.<break time="350ms"/>'
        "Fresh coffee delivered weekly.</speak>"
    )
    assert call["VoiceId"] == "Joanna"
    assert call["Engine"] == "neural"
    assert call["OutputFormat"] == "mp3"
    assert call["TextType"] == "ssml"


@pytest.mark.asyncio
async def test_pipeline_records_measured_duration_and_no_tempo_factor_when_in_range():
    version = _version()
    probe = _FakeDurationFfprobeRunner([15.0])
    pipeline = _pipeline(ffprobe_runner=probe)

    artifact = await pipeline.acquire(version, _never_cancelled)

    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/audio/voiceover"
    store: S3ArtifactStore = pipeline._store  # noqa: SLF001
    metadata = json.loads(store._client.objects[f"{prefix}.metadata.json"])  # noqa: SLF001
    assert metadata["measured_duration_seconds"] == 15.0
    assert metadata["tempo_factor"] is None
    assert artifact.provider == "polly"


@pytest.mark.asyncio
async def test_pipeline_applies_bounded_tempo_correction_for_duration_slightly_above_hard_max():
    # 20.4s is just above the 20s hard max -- a small, bounded speed-up
    # correction should bring it back in range rather than failing.
    version = _version()
    probe = _FakeDurationFfprobeRunner([20.4, 19.9])
    ffmpeg = _FakeTempoFfmpegRunner()
    pipeline = _pipeline(ffprobe_runner=probe, ffmpeg_runner=ffmpeg)

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.provider == "polly"
    assert len(ffmpeg.calls) == 1
    applied_filter = ffmpeg.calls[0][ffmpeg.calls[0].index("-af") + 1]
    assert applied_filter.startswith("atempo=")
    factor = float(applied_filter.removeprefix("atempo="))
    assert 0.92 <= factor <= 1.08

    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/audio/voiceover"
    store: S3ArtifactStore = pipeline._store  # noqa: SLF001
    metadata = json.loads(store._client.objects[f"{prefix}.metadata.json"])  # noqa: SLF001
    assert metadata["measured_duration_seconds"] == 19.9
    assert metadata["tempo_factor"] == pytest.approx(factor)


@pytest.mark.asyncio
async def test_pipeline_applies_bounded_tempo_correction_for_duration_slightly_below_hard_min():
    # 12.6s is just below the 13s hard min -- a small, bounded slow-down
    # correction should bring it back in range rather than failing.
    version = _version()
    probe = _FakeDurationFfprobeRunner([12.6, 13.1])
    ffmpeg = _FakeTempoFfmpegRunner()
    pipeline = _pipeline(ffprobe_runner=probe, ffmpeg_runner=ffmpeg)

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.provider == "polly"
    assert len(ffmpeg.calls) == 1
    applied_filter = ffmpeg.calls[0][ffmpeg.calls[0].index("-af") + 1]
    factor = float(applied_filter.removeprefix("atempo="))
    assert 0.92 <= factor <= 1.08

    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/audio/voiceover"
    store: S3ArtifactStore = pipeline._store  # noqa: SLF001
    metadata = json.loads(store._client.objects[f"{prefix}.metadata.json"])  # noqa: SLF001
    assert metadata["measured_duration_seconds"] == 13.1


@pytest.mark.asyncio
async def test_pipeline_rejects_far_out_of_range_duration_as_voiceover_step_failure():
    # ~22s requires a speed-up factor far beyond the safe +-8% bound -- must
    # fail here (attributed to the voiceover step by the caller's graph
    # wiring), not be aggressively stretched, and never reach the video step.
    version = _version()
    probe = _FakeDurationFfprobeRunner([22.0])
    ffmpeg = _FakeTempoFfmpegRunner()
    pipeline = _pipeline(ffprobe_runner=probe, ffmpeg_runner=ffmpeg)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)

    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert error.value.retryable is False
    assert ffmpeg.calls == []  # no mutation attempted -- rejected before any ffmpeg call


@pytest.mark.asyncio
async def test_pipeline_accepts_durations_across_the_whole_valid_range_without_tempo_calls():
    # 13.0, 15.0 (target), 17.4, 19.5, and 20.0 are all valid under the
    # 13-20s hard bound and must pass through completely untouched -- this
    # is the exact product correction: narration between 17s and 20s is
    # normal, not a failure.
    for duration in (13.0, 15.0, 17.4, 19.5, 20.0):
        version = _version()
        probe = _FakeDurationFfprobeRunner([duration])
        ffmpeg = _FakeTempoFfmpegRunner()
        pipeline = _pipeline(ffprobe_runner=probe, ffmpeg_runner=ffmpeg)

        artifact = await pipeline.acquire(version, _never_cancelled)

        assert artifact.provider == "polly", f"duration={duration}"
        assert ffmpeg.calls == [], f"duration={duration}: no tempo correction should be attempted"


@pytest.mark.asyncio
async def test_pipeline_requires_prior_storyboard():
    version = _version()
    version = version.model_copy(update={"storyboard": None})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="storyboard"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_maps_unrecognized_client_error_code_to_voice_provider_unavailable():
    version = _version()
    polly = _Polly(error=ClientError({"Error": {"Code": "SomethingUnexpected"}}, "SynthesizeSpeech"))
    pipeline = _pipeline(polly)
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_pipeline_maps_unreadable_audio_stream_to_invalid_provider_output():
    version = _version()

    class _BrokenStream:
        def read(self):
            raise OSError("stream closed")

    class _BrokenPolly:
        def synthesize_speech(self, **kwargs):
            return {"AudioStream": _BrokenStream()}

    pipeline = _pipeline(_BrokenPolly())
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_pipeline_resolves_voice_by_brief_language():
    polly = _Polly()
    pipeline = _pipeline(polly)
    await pipeline.acquire(_version(language="fr-FR"), _never_cancelled)
    assert polly.calls[0]["VoiceId"] == "Lea"


@pytest.mark.asyncio
async def test_pipeline_explicit_voice_id_overrides_language_mapping():
    polly = _Polly()
    pipeline = _pipeline(polly, voice_id="Matthew")
    await pipeline.acquire(_version(language="fr-FR"), _never_cancelled)
    assert polly.calls[0]["VoiceId"] == "Matthew"


@pytest.mark.asyncio
async def test_pipeline_rejects_unsupported_language_when_no_override_configured():
    pipeline = _pipeline()
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(_version(language="xx-XX"), _never_cancelled)
    assert error.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_pipeline_reconciles_existing_audio_and_skips_polly_call():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    polly = _Polly()
    pipeline = _pipeline(polly, store)
    first = await pipeline.acquire(version, _never_cancelled)
    assert len(polly.calls) == 1
    second = await pipeline.acquire(version, _never_cancelled)
    assert len(polly.calls) == 1
    assert second.artifact_id == first.artifact_id
    assert second.checksum_sha256 == first.checksum_sha256


@pytest.mark.asyncio
async def test_pipeline_regenerates_when_narration_fingerprint_changes():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    polly = _Polly()
    pipeline = _pipeline(polly, store)
    await pipeline.acquire(version, _never_cancelled)
    changed = version.model_copy(
        update={
            "storyboard": version.storyboard.model_copy(
                update={
                    "scenes": [
                        scene.model_copy(update={"narration": "A brand new script."})
                        for scene in version.storyboard.scenes
                    ]
                }
            )
        }
    )
    await pipeline.acquire(changed, _never_cancelled)
    assert len(polly.calls) == 2


@pytest.mark.asyncio
async def test_pipeline_rejects_oversized_narration_without_calling_polly():
    version = _version()
    long_scene = version.storyboard.scenes[0].model_copy(update={"narration": "x" * 3000})
    version = version.model_copy(
        update={
            "storyboard": version.storyboard.model_copy(update={"scenes": [long_scene, *version.storyboard.scenes[1:]]})
        }
    )
    polly = _Polly()
    pipeline = _pipeline(polly)
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert polly.calls == []


@pytest.mark.asyncio
async def test_pipeline_maps_throttling_and_auth_and_service_errors():
    version = _version()
    cases = [
        ({"Error": {"Code": "ThrottlingException"}}, "PROVIDER_THROTTLED", True),
        ({"Error": {"Code": "AccessDeniedException"}}, "VOICE_PROVIDER_UNAVAILABLE", False),
        ({"Error": {"Code": "ServiceUnavailableException"}}, "VOICE_PROVIDER_UNAVAILABLE", True),
        # A malformed-SSML business/product/message field is a deterministic
        # input problem (defense-in-depth alongside per-scene XML escaping),
        # not a transient provider issue, so it must not be retried.
        ({"Error": {"Code": "InvalidSsmlException"}}, "VOICE_PROVIDER_UNAVAILABLE", False),
    ]
    for error_response, code, retryable in cases:
        polly = _Polly(error=ClientError(error_response, "SynthesizeSpeech"))
        pipeline = _pipeline(polly)
        with pytest.raises(WorkflowOperationError) as error:
            await pipeline.acquire(version, _never_cancelled)
        assert error.value.code == code
        assert error.value.retryable is retryable


@pytest.mark.asyncio
async def test_pipeline_maps_read_timeout_to_provider_timeout():
    version = _version()
    polly = _Polly(error=ReadTimeoutError(endpoint_url="https://polly.us-east-1.amazonaws.com/"))
    pipeline = _pipeline(polly)
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_pipeline_maps_connect_timeout_to_provider_timeout():
    version = _version()
    polly = _Polly(error=ConnectTimeoutError(endpoint_url="https://polly.us-east-1.amazonaws.com/"))
    pipeline = _pipeline(polly)
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_pipeline_maps_endpoint_connection_error_to_voice_provider_unavailable():
    version = _version()
    polly = _Polly(error=EndpointConnectionError(endpoint_url="https://polly.us-east-1.amazonaws.com/"))
    pipeline = _pipeline(polly)
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_pipeline_maps_generic_botocore_error_to_voice_provider_unavailable_not_timeout():
    # A generic, non-timeout BotoCoreError must not be mislabeled as a Polly
    # timeout -- only ReadTimeoutError/ConnectTimeoutError map to PROVIDER_TIMEOUT.
    version = _version()
    polly = _Polly(error=BotoCoreError())
    pipeline = _pipeline(polly)
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert error.value.code != "PROVIDER_TIMEOUT"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_pipeline_maps_audio_stream_read_timeout_to_provider_timeout():
    version = _version()

    class _TimeoutStream:
        def read(self):
            raise ReadTimeoutError(endpoint_url="https://polly.us-east-1.amazonaws.com/")

    class _TimeoutPolly:
        def synthesize_speech(self, **kwargs):
            return {"AudioStream": _TimeoutStream()}

    pipeline = _pipeline(_TimeoutPolly())
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True


class _TrackedStream:
    def __init__(self, audio: bytes = b"", *, read_error: Exception | None = None) -> None:
        self._audio = audio
        self._read_error = read_error
        self.closed = False

    def read(self):
        if self._read_error:
            raise self._read_error
        return self._audio

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_pipeline_closes_audio_stream_on_success():
    version = _version()
    stream = _TrackedStream(_mp3_bytes())

    class _StreamPolly:
        def synthesize_speech(self, **kwargs):
            return {"AudioStream": stream}

    pipeline = _pipeline(_StreamPolly())
    await pipeline.acquire(version, _never_cancelled)
    assert stream.closed is True


@pytest.mark.asyncio
async def test_pipeline_closes_audio_stream_on_read_failure():
    version = _version()
    stream = _TrackedStream(read_error=OSError("stream closed"))

    class _StreamPolly:
        def synthesize_speech(self, **kwargs):
            return {"AudioStream": stream}

    pipeline = _pipeline(_StreamPolly())
    with pytest.raises(WorkflowOperationError):
        await pipeline.acquire(version, _never_cancelled)
    assert stream.closed is True


@pytest.mark.asyncio
async def test_pipeline_polly_call_does_not_block_event_loop():
    import time as _time

    class _SlowPolly:
        def synthesize_speech(self, **kwargs):
            _time.sleep(0.3)
            return {"AudioStream": io.BytesIO(_mp3_bytes())}

    version = _version()
    pipeline = _pipeline(_SlowPolly())
    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        for _ in range(30):
            await asyncio.sleep(0.01)
            ticks += 1

    await asyncio.gather(pipeline.acquire(version, _never_cancelled), _ticker())
    assert ticks > 5


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_polly_call():
    version = _version()
    polly = _Polly()
    pipeline = _pipeline(polly)
    calls = {"n": 0}

    async def cancel_before_polly():
        calls["n"] += 1
        return calls["n"] == 1

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_before_polly)
    assert "before_polly" in str(error.value)
    assert polly.calls == []


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_validation():
    version = _version()
    polly = _Polly()
    pipeline = _pipeline(polly)
    calls = {"n": 0}

    async def cancel_second_check():
        calls["n"] += 1
        return calls["n"] == 2

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_second_check)
    assert "before_validation" in str(error.value)


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_duration_check():
    version = _version()
    polly = _Polly()
    pipeline = _pipeline(polly)
    calls = {"n": 0}

    async def cancel_third_check():
        calls["n"] += 1
        return calls["n"] == 3

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_third_check)
    assert "before_duration_check" in str(error.value)


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_upload():
    version = _version()
    polly = _Polly()
    pipeline = _pipeline(polly)
    calls = {"n": 0}

    async def cancel_fourth_check():
        calls["n"] += 1
        return calls["n"] == 4

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_fourth_check)
    assert "before_s3_upload" in str(error.value)


# ---------------------------------------------------------------------------
# SSML construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_ssml_escapes_xml_special_characters_in_narration():
    version = _version()
    storyboard = version.storyboard.model_copy(
        update={
            "scenes": [
                scene.model_copy(update={"narration": "Tom & Jerry's Best Deal <ever>"})
                for scene in version.storyboard.scenes
            ]
        }
    )
    version = version.model_copy(update={"storyboard": storyboard})
    polly = _Polly()
    pipeline = _pipeline(polly)
    await pipeline.acquire(version, _never_cancelled)
    text = polly.calls[0]["Text"]
    assert "&amp;" in text
    assert "&lt;ever&gt;" in text
    # Document must be well-formed XML despite the raw & and < in source
    # narration -- proves escaping happens per-scene before <break> tags are
    # inserted, not on the already-tagged joined string.
    import xml.etree.ElementTree as ET

    ET.fromstring(text)


@pytest.mark.asyncio
async def test_pipeline_ssml_contains_exactly_two_breaks_between_three_scenes():
    version = _version()
    polly = _Polly()
    pipeline = _pipeline(polly)
    await pipeline.acquire(version, _never_cancelled)
    text = polly.calls[0]["Text"]
    assert text.count('<break time="350ms"/>') == 2


def test_build_ssml_never_emits_unsupported_neural_tags():
    from campaign_worker.audio.pipeline import _build_ssml

    text = _build_ssml(["Scene one.", "Scene two.", "Scene three."])
    assert "<emphasis" not in text
    assert "<prosody" not in text
    assert text.startswith("<speak>")
    assert text.endswith("</speak>")


# ---------------------------------------------------------------------------
# Fingerprint widening: must invalidate reconciliation when synthesis
# behavior changes, even if narration text is unchanged.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_regenerates_when_voice_id_changes_even_if_narration_unchanged():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    polly = _Polly()
    await _pipeline(polly, store, voice_id="Joanna").acquire(version, _never_cancelled)
    assert len(polly.calls) == 1
    await _pipeline(polly, store, voice_id="Matthew").acquire(version, _never_cancelled)
    assert len(polly.calls) == 2


@pytest.mark.asyncio
async def test_pipeline_regenerates_when_engine_changes_even_if_narration_unchanged():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    polly = _Polly()
    await _pipeline(polly, store, engine="neural").acquire(version, _never_cancelled)
    assert len(polly.calls) == 1
    await _pipeline(polly, store, engine="generative").acquire(version, _never_cancelled)
    assert len(polly.calls) == 2


@pytest.mark.asyncio
async def test_pipeline_regenerates_when_voice_synthesis_version_bumps(monkeypatch):
    import campaign_worker.audio.pipeline as pipeline_module

    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    polly = _Polly()
    pipeline = _pipeline(polly, store)
    await pipeline.acquire(version, _never_cancelled)
    assert len(polly.calls) == 1

    monkeypatch.setattr(pipeline_module, "_VOICE_SYNTHESIS_VERSION", 2)
    await pipeline.acquire(version, _never_cancelled)
    assert len(polly.calls) == 2


@pytest.mark.asyncio
async def test_pipeline_reconciles_when_voice_and_engine_and_version_are_all_unchanged():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    polly = _Polly()
    pipeline = _pipeline(polly, store, voice_id="Joanna", engine="neural")
    first = await pipeline.acquire(version, _never_cancelled)
    assert len(polly.calls) == 1
    second = await _pipeline(polly, store, voice_id="Joanna", engine="neural").acquire(version, _never_cancelled)
    assert len(polly.calls) == 1
    assert second.artifact_id == first.artifact_id


# ---------------------------------------------------------------------------
# Normalization integration: no silent fallback to raw/unnormalized audio.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_stores_normalized_audio_and_loudness_metadata():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    polly = _Polly()
    pipeline = _pipeline(polly, store)
    await pipeline.acquire(version, _never_cancelled)

    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/audio/voiceover"
    import json

    metadata = json.loads(s3.objects[f"{prefix}.metadata.json"])
    assert metadata["text_type"] == "ssml"
    assert metadata["ssml_break_ms"] == 350
    assert metadata["normalization_applied"] is True
    assert metadata["target_lufs"] == -16.0
    assert metadata["true_peak_ceiling_dbtp"] == -1.5
    assert metadata["measured_integrated_lufs"] == -16.20
    assert metadata["measured_true_peak_dbtp"] == -1.50


@pytest.mark.asyncio
async def test_pipeline_propagates_normalization_failure_without_falling_back_to_raw_audio():
    version = _version()
    polly = _Polly()

    class _FailingNormalizer:
        async def normalize(self, mp3_bytes: bytes):
            raise WorkflowOperationError("PROVIDER_TIMEOUT", "ffmpeg timed out", retryable=True)

    pipeline = _pipeline(polly, normalizer=_FailingNormalizer())
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True
    # Polly synthesis succeeded, but nothing further happened -- confirming
    # there's no fallback path that stores the raw, un-normalized bytes.
    assert len(polly.calls) == 1
