from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus, StepStatus, WorkflowStep
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.graph import nodes
from campaign_worker.graph.boundary import with_step_tracking
from campaign_worker.graph.state import GraphState
from campaign_worker.providers.base import ImageProvider
from campaign_worker.providers.mock_image_provider import MockImageProvider
from campaign_worker.providers.models import ImageGenerationRequest, ImageGenerationResult
from campaign_worker.repositories.workflow_repository import WorkflowRepository


def _version(**overrides):
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
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
    defaults = dict(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


@pytest.mark.asyncio
async def test_receive_request_and_analyze_campaign_are_passthrough_safe():
    state: GraphState = {"version": _version()}
    after_receive = await nodes.receive_request(state)
    after_analyze = await nodes.analyze_campaign(after_receive)
    assert after_analyze["version"].campaign_id == state["version"].campaign_id


@pytest.mark.asyncio
async def test_validate_input_rejects_blank_description():
    brief = _version().brief.model_copy(update={"business_description": "                    "})
    state: GraphState = {"version": _version(brief=brief)}
    with pytest.raises(ValueError, match="business_description"):
        await nodes.validate_input(state)


@pytest.mark.asyncio
async def test_validate_input_accepts_populated_description():
    state: GraphState = {"version": _version()}
    result = await nodes.validate_input(state)
    assert result["version"].campaign_id == state["version"].campaign_id


@pytest.mark.asyncio
async def test_create_strategy_produces_schema_valid_output():
    state: GraphState = {"version": _version()}
    result = await nodes.create_strategy(state)
    strategy = result["version"].strategy
    assert strategy is not None
    assert strategy.audience == "Urban professionals aged 25-40"
    assert "instagram" in strategy.channel_rationale
    assert "facebook" in strategy.channel_rationale


@pytest.mark.asyncio
async def test_create_strategy_falls_back_to_general_audience_when_unspecified():
    version = _version()
    brief = version.brief.model_copy(update={"target_audience": None})
    state: GraphState = {"version": version.model_copy(update={"brief": brief})}
    result = await nodes.create_strategy(state)
    assert result["version"].strategy.audience == "general audience"


@pytest.mark.asyncio
async def test_generate_copy_requires_prior_strategy():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="create_strategy"):
        await nodes.generate_copy(state)


@pytest.mark.asyncio
async def test_generate_copy_produces_schema_valid_output():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    result = await nodes.generate_copy(strategized)
    copy = result["version"].campaign_copy
    assert copy is not None
    assert copy.call_to_action == "Subscribe today"
    assert len(copy.channel_variants) == 2
    assert {variant.channel for variant in copy.channel_variants} == {"instagram", "facebook"}


@pytest.mark.asyncio
async def test_create_storyboard_requires_prior_strategy():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="create_strategy"):
        await nodes.create_storyboard(state)


@pytest.mark.asyncio
async def test_create_storyboard_produces_three_scenes_totaling_valid_duration():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    copied = await nodes.generate_copy(strategized)
    result = await nodes.create_storyboard(copied)
    storyboard = result["version"].storyboard
    assert storyboard is not None
    assert [s.scene_number for s in storyboard.scenes] == [1, 2, 3]
    assert storyboard.total_duration_seconds == sum(s.duration_seconds for s in storyboard.scenes)
    assert 13 <= storyboard.total_duration_seconds <= 17


async def _version_with_storyboard(**overrides) -> CampaignVersion:
    state: GraphState = {"version": _version(**overrides)}
    strategized = await nodes.create_strategy(state)
    copied = await nodes.generate_copy(strategized)
    result = await nodes.create_storyboard(copied)
    return result["version"]


class _FakeStepRepositoryForGenerateImages(WorkflowRepository):
    def __init__(self, seed: dict[tuple, WorkflowStepRecord] | None = None) -> None:
        self.steps: dict[tuple, WorkflowStepRecord] = dict(seed or {})
        self.save_calls: list[WorkflowStepRecord] = []

    async def get_step(self, campaign_id: UUID, campaign_version: int, step: WorkflowStep) -> WorkflowStepRecord | None:
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record: WorkflowStepRecord) -> None:
        self.save_calls.append(record)
        self.steps[(record.campaign_id, record.campaign_version, record.step)] = record

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


class _CountingMockImageProvider(ImageProvider):
    def __init__(self) -> None:
        self.calls = 0
        self._delegate = MockImageProvider()

    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.calls += 1
        return await self._delegate.generate_image(request)


@pytest.mark.asyncio
async def test_generate_images_requires_prior_storyboard():
    state: GraphState = {"version": _version()}
    node = nodes.make_generate_images_node(MockImageProvider())
    with pytest.raises(ValueError, match="create_storyboard"):
        await node(state)


@pytest.mark.asyncio
async def test_generate_images_produces_one_artifact_per_scene():
    version = await _version_with_storyboard()
    node = nodes.make_generate_images_node(MockImageProvider())
    result = await node({"version": version})

    artifacts = result["version"].image_artifacts
    assert len(artifacts) == 3
    assert [a.campaign_id for a in artifacts] == [version.campaign_id] * 3
    assert [a.campaign_version for a in artifacts] == [version.campaign_version] * 3


@pytest.mark.asyncio
async def test_generate_images_is_deterministic_with_mock_provider():
    version = await _version_with_storyboard()
    node = nodes.make_generate_images_node(MockImageProvider())

    first = await node({"version": version})
    second = await node({"version": version})

    first_checksums = [a.checksum_sha256 for a in first["version"].image_artifacts]
    second_checksums = [a.checksum_sha256 for a in second["version"].image_artifacts]
    assert first_checksums == second_checksums


@pytest.mark.asyncio
async def test_generate_images_propagates_provider_failure():
    version = await _version_with_storyboard()
    node = nodes.make_generate_images_node(_AlwaysFailsImageProvider())
    with pytest.raises(ValueError, match="image generation failed"):
        await node({"version": version})


@pytest.mark.asyncio
async def test_generate_images_is_provider_agnostic():
    version = await _version_with_storyboard()

    async def run_with(provider: ImageProvider):
        node = nodes.make_generate_images_node(provider)
        result = await node({"version": version})
        return result["version"].image_artifacts

    mock_artifacts = await run_with(MockImageProvider())
    counting_provider = _CountingMockImageProvider()
    counting_artifacts = await run_with(counting_provider)

    assert len(mock_artifacts) == len(counting_artifacts) == 3
    assert counting_provider.calls == 3


@pytest.mark.asyncio
async def test_generate_images_wrapped_with_step_tracking_runs_on_first_execution():
    version = await _version_with_storyboard()
    repository = _FakeStepRepositoryForGenerateImages()
    provider = _CountingMockImageProvider()
    wrapped = with_step_tracking(WorkflowStep.IMAGES, repository)(nodes.make_generate_images_node(provider))

    result = await wrapped({"version": version})

    assert provider.calls == 3
    assert len(result["version"].image_artifacts) == 3
    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING, StepStatus.SUCCEEDED]


@pytest.mark.asyncio
async def test_generate_images_wrapped_with_step_tracking_skips_when_already_succeeded():
    version = await _version_with_storyboard()
    now = datetime.now(UTC)
    repository = _FakeStepRepositoryForGenerateImages(
        seed={
            (version.campaign_id, version.campaign_version, WorkflowStep.IMAGES): WorkflowStepRecord(
                campaign_id=version.campaign_id,
                campaign_version=version.campaign_version,
                step=WorkflowStep.IMAGES,
                status=StepStatus.SUCCEEDED,
                created_at=now,
                updated_at=now,
            )
        }
    )
    provider = _CountingMockImageProvider()
    wrapped = with_step_tracking(WorkflowStep.IMAGES, repository)(nodes.make_generate_images_node(provider))

    result = await wrapped({"version": version})

    assert provider.calls == 0
    assert result["version"].image_artifacts == version.image_artifacts
    assert repository.save_calls == []
