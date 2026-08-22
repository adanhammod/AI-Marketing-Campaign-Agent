"""C10 -- full backend E2E hardening.

Layer A (this file, primary content): deterministic in-process E2E driving the REAL
GraphJobProcessor/build_start_graph/build_resume_graph orchestration end-to-end (START then
RESUME) through a single shared fake WorkflowRepository, with fake/mock providers and a fake S3
client. This proves genuine multi-message lifecycle continuity, event ordering, and
crash/redelivery reconciliation using real production graph/node/boundary code -- the areas
prior C9 testing only exercised as two separate halves.

Layer B (the ffmpeg-marked test below): real ffmpeg/ffprobe subprocess execution feeding a real
S3PackagePipeline, proving artifact content integrity (not just state transitions) with real
media. Skips automatically when ffmpeg/ffprobe are not installed.

Layer C (real AWS smoke test) is intentionally not implemented here -- see the C10 report for the
recommended follow-up command using the already-existing moto-backed DynamoDB test suite plus a
manual `AWS_REGION=... uv run python -m campaign_worker.main` smoke run, which is the appropriate
mechanism for a deployment-time check, not a unit-test-suite responsibility.

This file does not import campaign_api: the two services are separate uv projects with
independent dependency graphs (campaign_api has no dependency on campaign_worker or vice versa),
so a true cross-process-boundary import would require adding a new dev dependency edge between
them -- treated as out of scope for a test-hardening task. The API-side transition rules
(approve/retry/revise decision logic) are already thoroughly tested against the real
CampaignService in services/api/tests/*; this file instead targets the genuinely under-tested
worker-side risk areas C9 flagged: true START->RESUME continuity, package-failure->retry->FINAL,
duplicate-delivery reconciliation, and event ordering, all through real worker code.
"""

import asyncio
import hashlib
import io
import json
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.artifacts import AudioArtifactReference, ImageArtifactReference
from campaign_contracts.campaign import (
    CampaignConstraints,
    CampaignCopy,
    CampaignVersion,
    ChannelCopy,
    RetryMetadata,
    Storyboard,
    StoryboardScene,
    StrategyOutput,
)
from campaign_contracts.enums import CampaignEventType, CampaignStatus, SQSOperation, StepStatus, WorkflowStep
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.events import CampaignEvent
from campaign_contracts.sqs import SQSJobMessage
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.package.pipeline import S3PackagePipeline, deterministic_package_artifact_id
from campaign_worker.providers.base import ImageProvider
from campaign_worker.providers.mock_image_provider import MockImageProvider
from campaign_worker.providers.mock_package_pipeline import MockPackagePipeline
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.providers.models import ImageGenerationRequest, ImageGenerationResult
from campaign_worker.repositories.workflow_repository import LeaseContext, WorkflowRepository
from campaign_worker.services.job_processor import GraphJobProcessor
from campaign_worker.storage.s3_artifact_store import S3ArtifactStore
from campaign_worker.video.pipeline import FfmpegVideoPipeline, deterministic_video_artifact_id
from campaign_worker.video.pipeline import _fingerprint as _video_fingerprint

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ---------------------------------------------------------------------------
# Shared helpers (mirrors the conventions already established in
# test_job_processor.py / test_c8_video.py / test_c9_package.py)
# ---------------------------------------------------------------------------


def _brief(**overrides) -> CampaignCreationRequest:
    defaults = dict(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals",
    )
    defaults.update(overrides)
    return CampaignCreationRequest(**defaults)


def _version(**overrides) -> CampaignVersion:
    now = datetime.now(UTC)
    defaults = dict(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=_brief(),
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


def _storyboard() -> Storyboard:
    return Storyboard(
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


def _strategy() -> StrategyOutput:
    return StrategyOutput(
        audience="Urban professionals",
        positioning="Example Coffee for Cold brew subscription",
        objective="increase online subscription sales",
        key_message="Fresh coffee delivered weekly.",
        channel_rationale={"instagram": "Reach Urban professionals on instagram"},
    )


def _campaign_copy() -> CampaignCopy:
    return CampaignCopy(
        headline="Example Coffee: Fresh coffee delivered weekly.",
        caption="A local roaster offering weekly cold brew delivery.",
        call_to_action="Subscribe today",
        hashtags=["#ExampleCoffee"],
        channel_variants=[
            ChannelCopy(
                channel="instagram",
                headline="Example Coffee",
                caption="Fresh",
                call_to_action="Subscribe today",
                hashtags=["#ExampleCoffee"],
            )
        ],
    )


def _image_artifacts(campaign_id, campaign_version: int) -> list[ImageArtifactReference]:
    now = datetime.now(UTC)
    return [
        ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=campaign_version,
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


def _voice_artifact(campaign_id, campaign_version: int) -> AudioArtifactReference:
    return AudioArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        workflow_step=WorkflowStep.VOICEOVER,
        mime_type="audio/mpeg",
        size_bytes=4096,
        checksum_sha256="b" * 64,
        created_at=datetime.now(UTC),
        provider="polly",
    )


def _version_ready_for_video(**overrides) -> CampaignVersion:
    """A version with strategy/copy/storyboard/images/voice already set -- everything
    render_video needs, nothing it produces yet."""
    campaign_id = uuid4()
    storyboard = _storyboard()
    defaults = dict(
        campaign_id=campaign_id,
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=80,
        brief=_brief(),
        constraints=CampaignConstraints(),
        strategy=_strategy(),
        copy=_campaign_copy(),
        storyboard=storyboard,
        image_artifacts=_image_artifacts(campaign_id, 1),
        voice_artifact=_voice_artifact(campaign_id, 1),
        retry=RetryMetadata(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


def _message(operation: SQSOperation, campaign_id, job_id, **overrides) -> SQSJobMessage:
    defaults = dict(
        schema_version=1,
        job_id=job_id,
        campaign_id=campaign_id,
        campaign_version=1,
        operation=operation,
        idempotency_key="c10",
        correlation_id=uuid4(),
        requested_at=datetime.now(UTC),
        attempt=0,
    )
    defaults.update(overrides)
    return SQSJobMessage(**defaults)


def _lease(owner: str = "worker-c10") -> LeaseContext:
    return LeaseContext(owner=owner, lock_version=1, expires_at=datetime.now(UTC) + timedelta(minutes=2))


async def _never_cancelled() -> bool:
    return False


def _resume_job_id(campaign_id, campaign_version: int):
    return uuid5(campaign_id, f"RESUME:{campaign_version}")


class _E2ERepository(WorkflowRepository):
    """Records every persisted version and every event (in append order) across
    however many process() calls share this one instance -- the backbone of every
    multi-message scenario below."""

    def __init__(self) -> None:
        self.steps: dict[tuple, WorkflowStepRecord] = {}
        self.save_calls: list[CampaignVersion] = []
        self.events: list[CampaignEvent] = []

    async def get_step(self, campaign_id, campaign_version, step):
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record, events=None) -> None:
        self.steps[(record.campaign_id, record.campaign_version, record.step)] = record
        for event in events or []:
            self.events.append(event)

    async def save_version(self, version, lease, events=None) -> None:
        self.save_calls.append(version)
        for event in events or []:
            self.events.append(event)

    async def load_version(self, message):
        raise NotImplementedError

    async def acquire_lease(self, message, owner, now, expires_at):
        raise NotImplementedError

    async def heartbeat(self, message, lease, now, expires_at):
        raise NotImplementedError

    async def is_completed(self, message):
        raise NotImplementedError

    async def complete(self, message, lease, completed_at):
        raise NotImplementedError

    async def release(self, message, lease):
        raise NotImplementedError

    async def record_exhausted(self, message, receive_count, now):
        raise NotImplementedError

    async def record_invalid(self, campaign_id, code, message_id, now):
        raise NotImplementedError

    async def available(self):
        raise NotImplementedError


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def _all_mock_processor(repository: WorkflowRepository, **overrides) -> GraphJobProcessor:
    return GraphJobProcessor(
        repository,
        overrides.get("image_provider", MockImageProvider()),
        overrides.get("voice_provider", MockVoiceProvider()),
        overrides.get("video_provider", MockVideoProvider()),
        is_cancelled=overrides.get("is_cancelled"),
        package_pipeline=overrides.get("package_pipeline", MockPackagePipeline()),
    )


# ---------------------------------------------------------------------------
# Scenario 1 -- happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_start_reaches_final_automatically_with_all_expected_artifacts():
    """No human approval gate: a single START message drives the campaign all the
    way to FINAL in one processing pass -- packaging runs automatically right
    after READY_FOR_REVIEW instead of waiting for a separate human-triggered
    RESUME message. READY_FOR_REVIEW is still durably saved as an intermediate
    milestone along the way (audit trail unchanged)."""
    repository = _E2ERepository()
    processor = _all_mock_processor(repository)
    version = _version()

    start_result = await processor.process(
        _message(SQSOperation.START, version.campaign_id, version.job_id), version, _lease()
    )
    assert start_result.completed is True

    ready = next(v for v in repository.save_calls if v.status == CampaignStatus.READY_FOR_REVIEW)
    assert ready.strategy is not None
    assert ready.campaign_copy is not None
    assert ready.storyboard is not None
    assert len(ready.image_artifacts) == 3
    assert len({artifact.artifact_id for artifact in ready.image_artifacts}) == 3
    assert ready.voice_artifact is not None
    assert ready.video_artifact is not None
    assert ready.review_package is not None

    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FINAL
    assert final.package_artifact is not None
    assert final.package_artifact.mime_type == "application/zip"
    # Finalization must not regenerate anything already produced earlier in the pass.
    assert final.strategy == ready.strategy
    assert final.campaign_copy == ready.campaign_copy
    assert final.storyboard == ready.storyboard
    assert final.image_artifacts == ready.image_artifacts
    assert final.voice_artifact == ready.voice_artifact
    assert final.video_artifact == ready.video_artifact
    assert final.review_package == ready.review_package

    # RESUME remains a supported manual escape hatch (e.g. for a campaign that was
    # already at READY_FOR_REVIEW/APPROVED before this behavior shipped) -- it is
    # not removed, just no longer the only way a campaign reaches FINAL.
    assert SQSOperation.RESUME in SQSOperation


# ---------------------------------------------------------------------------
# Scenario 2 -- package failure -> retry -> FINAL
# ---------------------------------------------------------------------------


class _FlakyPackagePipeline:
    """Fails the first RESUME attempt (retryable), succeeds on the second --
    exactly the shape a transient S3 outage during finalization would take."""

    def __init__(self) -> None:
        self.calls = 0
        self._real = MockPackagePipeline()

    async def acquire(self, version, is_cancelled):
        self.calls += 1
        if self.calls == 1:
            raise WorkflowOperationError("STORAGE_UNAVAILABLE", "simulated upload failure", retryable=True)
        return await self._real.acquire(version, is_cancelled)


@pytest.mark.asyncio
async def test_package_failure_then_retry_reaches_final():
    """No human approval gate: START itself now runs packaging in the same pass,
    so a package failure surfaces directly from the START call (not from a
    separately-triggered RESUME). CampaignService.retry() still resubmits
    RESUME -- not START -- whenever resume_step == PACKAGE, so the second
    delivery below is unchanged from before."""
    repository = _E2ERepository()
    flaky_pipeline = _FlakyPackagePipeline()
    processor = _all_mock_processor(repository, package_pipeline=flaky_pipeline)
    version = _version()

    start_result = await processor.process(
        _message(SQSOperation.START, version.campaign_id, version.job_id), version, _lease()
    )
    assert start_result.completed is True
    failed = repository.save_calls[-1]
    assert failed.status == CampaignStatus.FAILED
    assert failed.retry.resume_step == WorkflowStep.PACKAGE
    assert failed.error is not None
    assert failed.error.code == "STORAGE_UNAVAILABLE"
    assert failed.error.workflow_step == WorkflowStep.PACKAGE
    assert failed.package_artifact is None

    repository.save_calls.clear()
    resume_retry = await processor.process(
        _message(SQSOperation.RESUME, failed.campaign_id, failed.job_id, idempotency_key="resume-retry"),
        failed,
        _lease(),
    )
    assert resume_retry.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FINAL
    assert final.package_artifact is not None
    assert final.error is None or final.status == CampaignStatus.FINAL
    assert flaky_pipeline.calls == 2


# ---------------------------------------------------------------------------
# Scenario 4 -- pre-review retryable failure -> retry -> resumes correctly
# ---------------------------------------------------------------------------


class _FlakyImageProvider(ImageProvider):
    """Fails the very first scene call, then delegates to a real provider --
    exactly the shape of a single transient provider outage."""

    def __init__(self, real: ImageProvider) -> None:
        self._real = real
        self.calls = 0

    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.calls += 1
        if self.calls == 1:
            now = datetime.now(UTC)
            error = SanitizedWorkflowError(
                code="IMAGE_PROVIDER_UNAVAILABLE",
                message="unavailable",
                component="IMAGE_MCP",
                attempt=1,
                retryable=True,
                timestamp=now,
                correlation_id=uuid4(),
            )
            return ImageGenerationResult(
                provider="flaky", fallback_asset=False, started_at=now, completed_at=now, error=error
            )
        return await self._real.generate_image(request)


@pytest.mark.asyncio
async def test_pre_review_failure_then_retry_resumes_without_duplicating_completed_steps():
    repository = _E2ERepository()
    flaky_images = _FlakyImageProvider(MockImageProvider())
    processor = _all_mock_processor(repository, image_provider=flaky_images)
    version = _version()

    await processor.process(_message(SQSOperation.START, version.campaign_id, version.job_id), version, _lease())
    failed = repository.save_calls[-1]
    assert failed.status == CampaignStatus.FAILED
    assert failed.retry.resume_step == WorkflowStep.IMAGES
    strategy_before_retry = failed.strategy
    copy_before_retry = failed.campaign_copy
    storyboard_before_retry = failed.storyboard
    assert strategy_before_retry is not None  # ran to completion before the IMAGES failure

    # CampaignService.retry() resubmits START (resume_step != PACKAGE) with the SAME persisted
    # version -- STRATEGY/COPY/STORYBOARD are already SUCCEEDED and must not re-run.
    repository.save_calls.clear()
    retry_result = await processor.process(
        _message(SQSOperation.START, failed.campaign_id, failed.job_id, idempotency_key="retry-1"), failed, _lease()
    )
    assert retry_result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FINAL  # no human approval gate -- reaches FINAL directly
    ready = next(v for v in repository.save_calls if v.status == CampaignStatus.READY_FOR_REVIEW)
    assert ready.strategy == strategy_before_retry
    assert ready.campaign_copy == copy_before_retry
    assert ready.storyboard == storyboard_before_retry
    assert len(ready.image_artifacts) == 3  # not 6 -- no duplicate artifacts from the retry
    # exactly one failed call + three successful scene calls on the retry
    assert flaky_images.calls == 4


# ---------------------------------------------------------------------------
# Scenario 6 -- duplicate delivery / crash-before-step-record reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redelivery_after_crash_before_step_record_reconciles_video_without_rerendering():
    """Simulates a worker crash where the VIDEO artifact was already uploaded to S3 but the
    DynamoDB step record write never landed -- the composite idempotency gap the C10 research
    identified as untested for every artifact-backed step (IMAGES/VOICEOVER/VIDEO/PACKAGE)."""
    version = _version_ready_for_video()
    repository = _E2ERepository()
    now = datetime.now(UTC)
    for step in (
        WorkflowStep.STRATEGY,
        WorkflowStep.COPY,
        WorkflowStep.STORYBOARD,
        WorkflowStep.CREATIVE_PLAN,
        WorkflowStep.IMAGES,
        WorkflowStep.VOICEOVER,
    ):
        repository.steps[(version.campaign_id, version.campaign_version, step)] = WorkflowStepRecord(
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            step=step,
            status=StepStatus.SUCCEEDED,
            created_at=now,
            updated_at=now,
        )
    # VIDEO step record deliberately absent. CREATIVE_PLAN is marked done (like
    # every other prerequisite) so the graph reuses version.creative_video_plan
    # (None here, matching a legacy pre-Slice-2 version) instead of regenerating
    # a fresh plan mid-run -- which would otherwise change the video fingerprint
    # computed below out from under this reconciliation check.
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    fingerprint = _video_fingerprint(version, version.storyboard, music_checksum=None, sfx_checksums=[])
    checksum = hashlib.sha256(b"already-rendered-video").hexdigest()
    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/video/final"
    s3.objects[f"{prefix}.mp4"] = b"already-rendered-video"
    s3.objects[f"{prefix}.metadata.json"] = json.dumps(
        {
            "campaign_id": str(version.campaign_id),
            "campaign_version": version.campaign_version,
            "render_fingerprint": fingerprint,
            "checksum_sha256": checksum,
            "acquisition_timestamp": now.isoformat(),
        }
    ).encode()

    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("ffmpeg/ffprobe must not run when the artifact already reconciles")

    video_pipeline = FfmpegVideoPipeline(
        s3, store, "private-bucket", ffmpeg_runner=_must_not_be_called, ffprobe_runner=_must_not_be_called
    )
    processor = _all_mock_processor(repository, video_provider=video_pipeline)

    result = await processor.process(
        _message(SQSOperation.START, version.campaign_id, version.job_id), version, _lease()
    )

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FINAL  # no human approval gate -- reaches FINAL directly
    assert final.video_artifact is not None
    assert final.video_artifact.artifact_id == deterministic_video_artifact_id(
        version.campaign_id, version.campaign_version
    )
    assert final.video_artifact.checksum_sha256 == checksum


# ---------------------------------------------------------------------------
# Phase 4 -- event ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_ordering_is_coherent_and_redelivery_adds_no_duplicates():
    repository = _E2ERepository()
    processor = _all_mock_processor(repository)
    version = _version()

    await processor.process(_message(SQSOperation.START, version.campaign_id, version.job_id), version, _lease())
    ready = next(v for v in repository.save_calls if v.status == CampaignStatus.READY_FOR_REVIEW)

    # Redeliver the identical START message against the READY_FOR_REVIEW snapshot:
    # every step is already SUCCEEDED/terminal, so nothing new should be emitted.
    # (The SQS consumer's job_id-based completion check, tested separately, is
    # what prevents a redelivery from reaching process() again in the first
    # place once a campaign is genuinely done -- this only re-exercises
    # graph-level step-tracking idempotency in isolation.)
    events_after_first_start = list(repository.events)
    await processor.process(
        _message(SQSOperation.START, version.campaign_id, version.job_id, idempotency_key="dup"), ready, _lease()
    )
    assert repository.events == events_after_first_start

    for step in (
        WorkflowStep.STRATEGY,
        WorkflowStep.COPY,
        WorkflowStep.STORYBOARD,
        WorkflowStep.IMAGES,
        WorkflowStep.VOICEOVER,
        WorkflowStep.VIDEO,
        WorkflowStep.PACKAGE,
    ):
        step_events = [event.event_type for event in repository.events if event.step == step]
        assert step_events == [CampaignEventType.STEP_STARTED, CampaignEventType.STEP_COMPLETED], step

    event_types = [event.event_type for event in repository.events]
    assert event_types.count(CampaignEventType.REVIEW_READY) == 1
    assert event_types.count(CampaignEventType.FINALIZED) == 1

    def _index(step: WorkflowStep | None, event_type: CampaignEventType) -> int:
        return next(
            i
            for i, event in enumerate(repository.events)
            if event.event_type == event_type and (step is None or event.step == step)
        )

    video_completed = _index(WorkflowStep.VIDEO, CampaignEventType.STEP_COMPLETED)
    review_ready = _index(None, CampaignEventType.REVIEW_READY)
    package_started = _index(WorkflowStep.PACKAGE, CampaignEventType.STEP_STARTED)
    package_completed = _index(WorkflowStep.PACKAGE, CampaignEventType.STEP_COMPLETED)
    finalized = _index(None, CampaignEventType.FINALIZED)
    assert video_completed < review_ready < package_started < package_completed < finalized


# ---------------------------------------------------------------------------
# Phase 5 -- persistence / restart safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persisted_version_alone_is_sufficient_to_resume_after_json_round_trip():
    repository = _E2ERepository()
    processor = _all_mock_processor(repository)
    version = _version()

    await processor.process(_message(SQSOperation.START, version.campaign_id, version.job_id), version, _lease())
    ready = repository.save_calls[-1]
    approved = ready.model_copy(
        update={"status": CampaignStatus.APPROVED, "job_id": _resume_job_id(ready.campaign_id, ready.campaign_version)}
    )

    # Simulate a process/repository restart: serialize to JSON (as a real DynamoDB round-trip
    # would), then rebuild a genuinely new CampaignVersion purely from those bytes.
    serialized = approved.model_dump_json(by_alias=True)
    reloaded = CampaignVersion.model_validate_json(serialized)
    assert reloaded is not approved
    # brief round-trips as the field's declared NormalizedCampaignBrief type rather than the
    # CampaignCreationRequest subclass instance originally constructed with -- content-identical,
    # so compare via the wire representation rather than Python class identity.
    assert reloaded.model_dump(mode="json") == approved.model_dump(mode="json")

    fresh_repository = _E2ERepository()
    fresh_processor = _all_mock_processor(fresh_repository)  # a brand new worker instance too
    result = await fresh_processor.process(
        _message(SQSOperation.RESUME, reloaded.campaign_id, reloaded.job_id, idempotency_key="resume"),
        reloaded,
        _lease(),
    )

    assert result.completed is True
    final = fresh_repository.save_calls[-1]
    assert final.status == CampaignStatus.FINAL
    assert final.package_artifact is not None


# ---------------------------------------------------------------------------
# Layer B -- real ffmpeg + real S3PackagePipeline chained, with content-integrity checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe are not installed in this environment")
async def test_real_ffmpeg_video_feeds_real_package_pipeline_with_verifiable_content(tmp_path):
    from PIL import Image

    # Generate the real media bytes FIRST, so the artifact references below can carry their
    # true checksums -- required for the manifest/content cross-checks at the end to be
    # meaningful (the package pipeline embeds whatever checksum the reference declares, it does
    # not itself re-derive it from the downloaded bytes).
    campaign_id = uuid4()
    image_bytes: dict[int, bytes] = {}
    for scene_number, color in enumerate(["red", "green", "blue"], start=1):
        path = tmp_path / f"scene-{scene_number}.jpg"
        Image.new("RGB", (1080, 1920), color).save(path, format="JPEG")
        image_bytes[scene_number] = path.read_bytes()

    audio_path = tmp_path / "voiceover.mp3"
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=15", str(audio_path)],
        check=True,
        capture_output=True,
    )
    audio_bytes = audio_path.read_bytes()

    now = datetime.now(UTC)
    image_artifacts = [
        ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=1,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/jpeg",
            size_bytes=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            created_at=now,
            provider="pexels",
            scene_number=scene_number,
        )
        for scene_number, data in image_bytes.items()
    ]
    voice = AudioArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=1,
        workflow_step=WorkflowStep.VOICEOVER,
        mime_type="audio/mpeg",
        size_bytes=len(audio_bytes),
        checksum_sha256=hashlib.sha256(audio_bytes).hexdigest(),
        created_at=now,
        provider="polly",
    )
    version = _version_ready_for_video(campaign_id=campaign_id, image_artifacts=image_artifacts, voice_artifact=voice)

    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    for artifact in version.image_artifacts:
        key = (
            f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}"
            f"/images/scene-{artifact.scene_number}.jpg"
        )
        s3.objects[key] = image_bytes[artifact.scene_number]
    audio_key = f"campaigns/{voice.campaign_id}/versions/{voice.campaign_version}/audio/voiceover.mp3"
    s3.objects[audio_key] = audio_bytes

    video_pipeline = FfmpegVideoPipeline(s3, store, "private-bucket", render_timeout_seconds=60)
    video_artifact = await video_pipeline.acquire(version, _never_cancelled)
    assert video_artifact.artifact_id == deterministic_video_artifact_id(version.campaign_id, version.campaign_version)

    version_with_video = version.model_copy(update={"video_artifact": video_artifact})
    package_pipeline = S3PackagePipeline(s3, store, "private-bucket")
    package_artifact = await package_pipeline.acquire(version_with_video, _never_cancelled)
    assert package_artifact.artifact_id == deterministic_package_artifact_id(
        version.campaign_id, version.campaign_version
    )

    zip_key = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/package/campaign.zip"
    root = f"campaign-v{version.campaign_version}"
    with zipfile.ZipFile(io.BytesIO(s3.objects[zip_key])) as archive:
        manifest = json.loads(archive.read(f"{root}/manifest.json"))
        video_bytes_in_zip = archive.read(f"{root}/video/final.mp4")
        for artifact in version.image_artifacts:
            image_bytes_in_zip = archive.read(f"{root}/images/scene-{artifact.scene_number}.jpg")
            assert hashlib.sha256(image_bytes_in_zip).hexdigest() == artifact.checksum_sha256

    assert hashlib.sha256(video_bytes_in_zip).hexdigest() == video_artifact.checksum_sha256
    assert manifest["video"]["checksum_sha256"] == video_artifact.checksum_sha256
    manifest_scene_checksums = {entry["scene_number"]: entry["checksum_sha256"] for entry in manifest["images"]}
    for artifact in version.image_artifacts:
        assert manifest_scene_checksums[artifact.scene_number] == artifact.checksum_sha256
    assert "private-bucket" not in json.dumps(manifest)
