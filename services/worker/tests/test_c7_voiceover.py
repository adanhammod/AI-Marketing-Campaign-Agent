import hashlib
import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, Storyboard, StoryboardScene
from campaign_contracts.enums import CampaignStatus, WorkflowStep

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


def _pipeline(polly=None, store=None, **overrides) -> PollyVoicePipeline:
    return PollyVoicePipeline(
        polly or _Polly(),
        store or S3ArtifactStore(_S3(), "private-bucket"),
        AudioProcessor(),
        voice_id=overrides.get("voice_id"),
        engine=overrides.get("engine", "neural"),
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
    assert (
        call["Text"] == "Fresh coffee delivered weekly. Fresh coffee delivered weekly. Fresh coffee delivered weekly."
    )
    assert call["VoiceId"] == "Joanna"
    assert call["Engine"] == "neural"
    assert call["OutputFormat"] == "mp3"


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
    ]
    for error_response, code, retryable in cases:
        polly = _Polly(error=ClientError(error_response, "SynthesizeSpeech"))
        pipeline = _pipeline(polly)
        with pytest.raises(WorkflowOperationError) as error:
            await pipeline.acquire(version, _never_cancelled)
        assert error.value.code == code
        assert error.value.retryable is retryable


@pytest.mark.asyncio
async def test_pipeline_maps_timeout_to_provider_timeout():
    version = _version()
    polly = _Polly(error=BotoCoreError())
    pipeline = _pipeline(polly)
    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True


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
async def test_pipeline_cancellation_before_upload():
    version = _version()
    polly = _Polly()
    pipeline = _pipeline(polly)
    calls = {"n": 0}

    async def cancel_third_check():
        calls["n"] += 1
        return calls["n"] == 3

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_third_check)
    assert "before_s3_upload" in str(error.value)
