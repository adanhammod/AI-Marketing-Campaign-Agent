from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus, RevisionTarget, SQSOperation, WorkflowStep
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.sqs import SQSJobMessage

from campaign_worker.config import Settings
from campaign_worker.consumer.sqs_consumer import MessageOutcome, SQSConsumer
from campaign_worker.providers.base import ImageProvider, VideoProvider
from campaign_worker.providers.mock_image_provider import MockImageProvider
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.providers.models import (
    ImageGenerationRequest,
    ImageGenerationResult,
    VideoRenderRequest,
    VideoRenderResult,
)
from campaign_worker.repositories.workflow_repository import LeaseContext, WorkflowRepository
from campaign_worker.services.job_processor import GraphJobProcessor


def _brief(**overrides):
    defaults = dict(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram", "facebook"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals aged 25-40",
        call_to_action="Subscribe today",
    )
    defaults.update(overrides)
    return CampaignCreationRequest(**defaults)


def _version(**overrides):
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


def _message(operation, campaign_id, job_id, **overrides):
    defaults = dict(
        schema_version=1,
        job_id=job_id,
        campaign_id=campaign_id,
        campaign_version=1,
        operation=operation,
        idempotency_key="task21",
        correlation_id=uuid4(),
        requested_at=datetime.now(UTC),
        attempt=0,
    )
    defaults.update(overrides)
    return SQSJobMessage(**defaults)


def _lease(owner="worker-a"):
    return LeaseContext(owner=owner, lock_version=1, expires_at=datetime.now(UTC) + timedelta(minutes=2))


class _RecordingRepository(WorkflowRepository):
    def __init__(self) -> None:
        self.steps: dict[tuple, object] = {}
        self.save_calls: list[CampaignVersion] = []

    async def get_step(self, campaign_id, campaign_version, step):
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record):
        self.steps[(record.campaign_id, record.campaign_version, record.step)] = record

    async def save_version(self, version, lease):
        self.save_calls.append(version)

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


class _AlwaysFailsImageProvider(ImageProvider):
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
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
            provider="always-fails", fallback_asset=False, started_at=now, completed_at=now, error=error
        )


class _AlwaysFailsVideoProvider(VideoProvider):
    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult:
        now = datetime.now(UTC)
        error = SanitizedWorkflowError(
            code="VIDEO_PROVIDER_UNAVAILABLE",
            message="unavailable",
            component="HYPERFRAMES_MCP",
            attempt=1,
            retryable=True,
            timestamp=now,
            correlation_id=uuid4(),
        )
        return VideoRenderResult(
            provider="always-fails", fallback_asset=False, started_at=now, completed_at=now, error=error
        )


class _FailingStepRepository(_RecordingRepository):
    """Fails save_step's RUNNING marker for a chosen step, simulating a failure with no
    natural provider-level injection point (e.g. the pure create_strategy node)."""

    def __init__(self, failing_step: WorkflowStep) -> None:
        super().__init__()
        self._failing_step = failing_step

    async def save_step(self, record):
        if record.step == self._failing_step:
            raise RuntimeError(f"simulated persistence failure for {self._failing_step}")
        await super().save_step(record)


def _processor(image_provider=None, video_provider=None, is_cancelled=None, repository=None) -> GraphJobProcessor:
    repository = repository or _RecordingRepository()
    processor = GraphJobProcessor(
        repository,
        image_provider or MockImageProvider(),
        MockVoiceProvider(),
        video_provider or MockVideoProvider(),
        is_cancelled=is_cancelled,
    )
    return repository, processor


@pytest.mark.asyncio
async def test_start_processes_full_pipeline_and_reaches_ready_for_review():
    repository, processor = _processor()
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.READY_FOR_REVIEW
    assert final.strategy is not None
    assert final.campaign_copy is not None
    assert final.storyboard is not None
    assert len(final.image_artifacts) == 3
    assert final.video_artifact is not None
    assert final.error is None
    assert final.retry.resume_step is None


@pytest.mark.asyncio
async def test_start_persists_the_version_progressively_not_only_once():
    repository, processor = _processor()
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    await processor.process(message, version, _lease())

    assert len(repository.save_calls) > 1


@pytest.mark.asyncio
async def test_start_persisted_steps_are_step_tracked_for_skip_reuse():
    repository, processor = _processor()
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    await processor.process(message, version, _lease())

    for step in (
        WorkflowStep.STRATEGY,
        WorkflowStep.COPY,
        WorkflowStep.STORYBOARD,
        WorkflowStep.IMAGES,
        WorkflowStep.VIDEO,
    ):
        assert (version.campaign_id, version.campaign_version, step) in repository.steps


@pytest.mark.asyncio
async def test_resume_runs_only_prepare_final_package():
    repository, processor = _processor()
    version = _version()
    start_message = _message(SQSOperation.START, version.campaign_id, version.job_id)
    await processor.process(start_message, version, _lease())
    approved = repository.save_calls[-1].model_copy(update={"status": CampaignStatus.APPROVED})
    repository.save_calls.clear()

    resume_message = _message(SQSOperation.RESUME, approved.campaign_id, approved.job_id, idempotency_key="resume")
    result = await processor.process(resume_message, approved, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FINAL
    assert final.review_package is not None
    assert final.strategy == approved.strategy  # untouched, not regenerated
    assert final.error is None
    assert final.retry.resume_step is None
    # single-node graph: astream yields far fewer chunks than the 10+ node START path
    assert len(repository.save_calls) <= 2


@pytest.mark.asyncio
async def test_regenerate_is_rejected_cleanly_through_handle_failure():
    repository, processor = _processor()
    version = _version()
    message = _message(
        SQSOperation.REGENERATE,
        version.campaign_id,
        version.job_id,
        requested_step=WorkflowStep.STRATEGY,
        revision_scope=RevisionTarget.STRATEGY,
    )

    result = await processor.process(message, version, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FAILED
    assert "REGENERATE" in final.error.message


@pytest.mark.asyncio
async def test_images_failure_records_images_as_the_resume_step():
    repository, processor = _processor(image_provider=_AlwaysFailsImageProvider())
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FAILED
    assert final.error is not None
    assert final.retry.attempt == 1
    assert final.retry.retryable is True
    assert final.retry.resume_step == WorkflowStep.IMAGES
    assert final.error.workflow_step == WorkflowStep.IMAGES
    # progress made before the failing node is preserved, thanks to progressive persistence
    assert final.strategy is not None
    assert final.campaign_copy is not None
    assert final.storyboard is not None


@pytest.mark.asyncio
async def test_video_failure_records_video_as_the_resume_step():
    repository, processor = _processor(video_provider=_AlwaysFailsVideoProvider())
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FAILED
    assert final.retry.resume_step == WorkflowStep.VIDEO
    assert final.error.workflow_step == WorkflowStep.VIDEO
    # progress made before the failing node (including images) is preserved
    assert final.strategy is not None
    assert len(final.image_artifacts) == 3


@pytest.mark.asyncio
async def test_strategy_failure_records_strategy_as_the_resume_step():
    repository, processor = _processor(repository=_FailingStepRepository(WorkflowStep.STRATEGY))
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.FAILED
    assert final.retry.resume_step == WorkflowStep.STRATEGY
    assert final.error.workflow_step == WorkflowStep.STRATEGY
    # strategy never actually ran
    assert final.strategy is None


@pytest.mark.asyncio
async def test_cancellation_is_persisted_as_cancelled():
    async def always_cancelled() -> bool:
        return True

    repository, processor = _processor(is_cancelled=always_cancelled)
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.CANCELLED
    assert final.error.code == "CANCELLED_BY_USER"
    # cancelled at the very first node, which has no WorkflowStep mapping
    assert final.error.workflow_step is None


@pytest.mark.asyncio
async def test_cancellation_during_a_step_tracked_node_records_the_correct_workflow_step():
    calls = 0

    async def cancelled_starting_at_create_strategy() -> bool:
        nonlocal calls
        calls += 1
        # receive_request, validate_input, analyze_campaign run normally (calls 1-3);
        # cancellation is flagged exactly when create_strategy is about to run (call 4).
        return calls > 3

    repository, processor = _processor(is_cancelled=cancelled_starting_at_create_strategy)
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    assert result.completed is True
    final = repository.save_calls[-1]
    assert final.status == CampaignStatus.CANCELLED
    assert final.error.code == "CANCELLED_BY_USER"
    assert final.error.workflow_step == WorkflowStep.STRATEGY


class _FullFakeRepository(WorkflowRepository):
    def __init__(self, version: CampaignVersion) -> None:
        self.version = version
        self.lease: LeaseContext | None = None
        self.completed = False
        self.steps: dict[tuple, object] = {}
        self.saved_versions: list[CampaignVersion] = []

    async def load_version(self, message):
        return self.version

    async def acquire_lease(self, message, owner, now, expires_at):
        self.lease = LeaseContext(owner, 2, expires_at)
        return self.lease

    async def heartbeat(self, message, lease, now, expires_at):
        self.lease = LeaseContext(lease.owner, lease.lock_version + 1, expires_at)
        return self.lease

    async def is_completed(self, message):
        return self.completed

    async def complete(self, message, lease, completed_at):
        self.completed = True

    async def release(self, message, lease):
        pass

    async def record_exhausted(self, message, receive_count, now):
        pass

    async def record_invalid(self, campaign_id, code, message_id, now):
        pass

    async def available(self):
        return True

    async def get_step(self, campaign_id, campaign_version, step):
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record):
        self.steps[(record.campaign_id, record.campaign_version, record.step)] = record

    async def save_version(self, version, lease):
        self.saved_versions.append(version)
        self.version = version


class _StubSQSClient:
    def __init__(self) -> None:
        self.deletes: list[str] = []
        self.visibility_calls = 0

    def delete_message(self, **kwargs):
        self.deletes.append(kwargs["ReceiptHandle"])
        return {}

    def change_message_visibility(self, **kwargs):
        self.visibility_calls += 1
        return {}

    def receive_message(self, **kwargs):
        return {"Messages": []}

    def get_queue_attributes(self, **kwargs):
        return {}


def _settings() -> Settings:
    return Settings(aws_region="us-east-1", queue_url="https://sqs.example/queue", table_name="campaign-table")


def _raw(message: SQSJobMessage, count: int = 1) -> dict:
    return {
        "MessageId": "transport-id",
        "ReceiptHandle": "opaque-receipt",
        "Body": message.model_dump_json(),
        "Attributes": {"ApproximateReceiveCount": str(count)},
    }


@pytest.mark.asyncio
async def test_integration_start_message_is_processed_and_acknowledged():
    version = _version()
    repository = _FullFakeRepository(version)
    processor = GraphJobProcessor(repository, MockImageProvider(), MockVoiceProvider(), MockVideoProvider())
    consumer = SQSConsumer(_StubSQSClient(), repository, processor, _settings())
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    outcome = await consumer.process_raw(_raw(message))

    assert outcome == MessageOutcome.ACKNOWLEDGED
    assert repository.version.status == CampaignStatus.READY_FOR_REVIEW
    assert repository.completed is True


@pytest.mark.asyncio
async def test_integration_resume_message_is_processed_and_acknowledged():
    version = _version()
    repository = _FullFakeRepository(version)
    processor = GraphJobProcessor(repository, MockImageProvider(), MockVoiceProvider(), MockVideoProvider())
    start_message = _message(SQSOperation.START, version.campaign_id, version.job_id, idempotency_key="start")
    await processor.process(start_message, version, _lease())
    repository.version = repository.saved_versions[-1].model_copy(update={"status": CampaignStatus.APPROVED})
    repository.completed = False

    consumer = SQSConsumer(_StubSQSClient(), repository, processor, _settings())
    resume_message = _message(SQSOperation.RESUME, version.campaign_id, version.job_id, idempotency_key="resume")

    outcome = await consumer.process_raw(_raw(resume_message))

    assert outcome == MessageOutcome.ACKNOWLEDGED
    assert repository.version.status == CampaignStatus.FINAL
    assert repository.version.review_package is not None


@pytest.mark.asyncio
async def test_integration_duplicate_message_does_not_reprocess():
    version = _version()
    repository = _FullFakeRepository(version)
    repository.completed = True
    processor = GraphJobProcessor(repository, MockImageProvider(), MockVoiceProvider(), MockVideoProvider())
    consumer = SQSConsumer(_StubSQSClient(), repository, processor, _settings())
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    outcome = await consumer.process_raw(_raw(message))

    assert outcome == MessageOutcome.ACKNOWLEDGED
    assert repository.saved_versions == []
