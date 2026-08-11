import ast
import inspect
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata
from campaign_contracts.enums import CampaignStatus, ErrorComponent, StepStatus, WorkflowStep
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.graph import nodes
from campaign_worker.graph.boundary import NodeCancelled, with_step_tracking
from campaign_worker.graph.state import GraphState
from campaign_worker.providers.base import ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.providers.mock_image_provider import MockImageProvider
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.providers.models import (
    ImageGenerationRequest,
    ImageGenerationResult,
    VideoRenderRequest,
    VideoRenderResult,
)
from campaign_worker.providers.voice_models import VoiceGenerationRequest, VoiceGenerationResult
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

    async def save_step(self, record: WorkflowStepRecord, events=None) -> None:
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

    async def save_version(self, version, lease, events=None):
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


class _AlwaysFailsVoiceProvider(VoiceProvider):
    async def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        now = datetime.now(UTC)
        error = SanitizedWorkflowError(
            code="INTERNAL_ERROR",
            message="unavailable",
            component="UNKNOWN",
            attempt=1,
            retryable=True,
            timestamp=now,
            correlation_id=uuid4(),
        )
        return VoiceGenerationResult(
            provider="always-fails", fallback_asset=False, started_at=now, completed_at=now, error=error
        )


class _CountingMockVoiceProvider(VoiceProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.received_requests: list[VoiceGenerationRequest] = []
        self._delegate = MockVoiceProvider()

    async def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        self.received_requests.append(request)
        return await self._delegate.generate_voice(request)


@pytest.mark.asyncio
async def test_generate_voiceover_requires_prior_storyboard():
    state: GraphState = {"version": _version()}
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())
    with pytest.raises(ValueError, match="create_storyboard"):
        await node(state)


@pytest.mark.asyncio
async def test_generate_voiceover_produces_a_voice_artifact_in_graph_state():
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())

    result = await node({"version": version})

    voice_artifact = result["voice_artifact"]
    assert voice_artifact.campaign_id == version.campaign_id
    assert voice_artifact.campaign_version == version.campaign_version
    assert voice_artifact.artifact_type.value == "AUDIO"


@pytest.mark.asyncio
async def test_generate_voiceover_does_not_persist_to_campaign_version():
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())

    result = await node({"version": version})

    assert result["version"] is version
    assert not hasattr(result["version"], "voice_artifact")


@pytest.mark.asyncio
async def test_generate_voiceover_combines_narration_from_all_scenes():
    version = await _version_with_storyboard()
    provider = _CountingMockVoiceProvider()
    node = nodes.make_generate_voiceover_node(provider)

    await node({"version": version})

    expected_narration = " ".join(scene.narration for scene in version.storyboard.scenes)
    assert provider.received_requests[0].narration_text == expected_narration


@pytest.mark.asyncio
async def test_generate_voiceover_is_deterministic_with_mock_provider():
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())

    first = await node({"version": version})
    second = await node({"version": version})

    assert first["voice_artifact"].checksum_sha256 == second["voice_artifact"].checksum_sha256


@pytest.mark.asyncio
async def test_generate_voiceover_propagates_provider_failure():
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(_AlwaysFailsVoiceProvider())
    with pytest.raises(ValueError, match="voice generation failed"):
        await node({"version": version})


@pytest.mark.asyncio
async def test_generate_voiceover_is_provider_agnostic():
    version = await _version_with_storyboard()

    async def run_with(provider: VoiceProvider):
        node = nodes.make_generate_voiceover_node(provider)
        result = await node({"version": version})
        return result["voice_artifact"]

    mock_artifact = await run_with(MockVoiceProvider())
    counting_provider = _CountingMockVoiceProvider()
    counting_artifact = await run_with(counting_provider)

    assert mock_artifact.checksum_sha256 == counting_artifact.checksum_sha256
    assert counting_provider.calls == 1


async def _state_with_images_and_voice() -> GraphState:
    version = await _version_with_storyboard()
    images_result = await nodes.make_generate_images_node(MockImageProvider())({"version": version})
    voice_result = await nodes.make_generate_voiceover_node(MockVoiceProvider())({"version": version})
    return {"version": images_result["version"], "voice_artifact": voice_result["voice_artifact"]}


class _AlwaysFailsVideoProvider(VideoProvider):
    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult:
        now = datetime.now(UTC)
        error = SanitizedWorkflowError(
            code="VIDEO_PROVIDER_UNAVAILABLE",
            message="unavailable",
            component="UNKNOWN",
            attempt=1,
            retryable=True,
            timestamp=now,
            correlation_id=uuid4(),
        )
        return VideoRenderResult(
            provider="always-fails", fallback_asset=False, started_at=now, completed_at=now, error=error
        )


class _CountingMockVideoProvider(VideoProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.received_requests: list[VideoRenderRequest] = []
        self._delegate = MockVideoProvider()

    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult:
        self.calls += 1
        self.received_requests.append(request)
        return await self._delegate.render_video(request)


@pytest.mark.asyncio
async def test_render_video_requires_prior_storyboard():
    state: GraphState = {"version": _version()}
    node = nodes.make_render_video_node(MockVideoProvider())
    with pytest.raises(ValueError, match="create_storyboard"):
        await node(state)


@pytest.mark.asyncio
async def test_render_video_requires_prior_generate_images():
    version = await _version_with_storyboard()
    voice_result = await nodes.make_generate_voiceover_node(MockVoiceProvider())({"version": version})
    state: GraphState = {"version": version, "voice_artifact": voice_result["voice_artifact"]}
    node = nodes.make_render_video_node(MockVideoProvider())
    with pytest.raises(ValueError, match="generate_images"):
        await node(state)


@pytest.mark.asyncio
async def test_render_video_requires_prior_generate_voiceover():
    version = await _version_with_storyboard()
    images_result = await nodes.make_generate_images_node(MockImageProvider())({"version": version})
    node = nodes.make_render_video_node(MockVideoProvider())
    with pytest.raises(ValueError, match="generate_voiceover"):
        await node({"version": images_result["version"]})


@pytest.mark.asyncio
async def test_render_video_produces_video_artifact_persisted_on_campaign_version():
    state = await _state_with_images_and_voice()
    node = nodes.make_render_video_node(MockVideoProvider())

    result = await node(state)

    video_artifact = result["version"].video_artifact
    assert video_artifact is not None
    assert video_artifact.campaign_id == state["version"].campaign_id
    assert video_artifact.campaign_version == state["version"].campaign_version


@pytest.mark.asyncio
async def test_render_video_forwards_voice_artifact_to_the_provider_request():
    state = await _state_with_images_and_voice()
    provider = _CountingMockVideoProvider()
    node = nodes.make_render_video_node(provider)

    await node(state)

    assert provider.received_requests[0].voice_artifact == state["voice_artifact"]


@pytest.mark.asyncio
async def test_render_video_is_deterministic_with_mock_provider():
    state = await _state_with_images_and_voice()
    node = nodes.make_render_video_node(MockVideoProvider())

    first = await node(state)
    second = await node(state)

    assert first["version"].video_artifact.checksum_sha256 == second["version"].video_artifact.checksum_sha256


@pytest.mark.asyncio
async def test_render_video_propagates_provider_failure():
    state = await _state_with_images_and_voice()
    node = nodes.make_render_video_node(_AlwaysFailsVideoProvider())
    with pytest.raises(ValueError, match="video rendering failed"):
        await node(state)


@pytest.mark.asyncio
async def test_render_video_is_provider_agnostic():
    state = await _state_with_images_and_voice()

    async def run_with(provider: VideoProvider):
        node = nodes.make_render_video_node(provider)
        result = await node(state)
        return result["version"].video_artifact

    mock_artifact = await run_with(MockVideoProvider())
    counting_provider = _CountingMockVideoProvider()
    counting_artifact = await run_with(counting_provider)

    assert mock_artifact.checksum_sha256 == counting_artifact.checksum_sha256
    assert counting_provider.calls == 1


@pytest.mark.asyncio
async def test_render_video_wrapped_with_step_tracking_runs_on_first_execution():
    state = await _state_with_images_and_voice()
    repository = _FakeStepRepositoryForGenerateImages()
    provider = _CountingMockVideoProvider()
    wrapped = with_step_tracking(WorkflowStep.VIDEO, repository)(nodes.make_render_video_node(provider))

    result = await wrapped(state)

    assert provider.calls == 1
    assert result["version"].video_artifact is not None
    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING, StepStatus.SUCCEEDED]


@pytest.mark.asyncio
async def test_render_video_wrapped_with_step_tracking_skips_when_already_succeeded():
    state = await _state_with_images_and_voice()
    version = state["version"]
    now = datetime.now(UTC)
    repository = _FakeStepRepositoryForGenerateImages(
        seed={
            (version.campaign_id, version.campaign_version, WorkflowStep.VIDEO): WorkflowStepRecord(
                campaign_id=version.campaign_id,
                campaign_version=version.campaign_version,
                step=WorkflowStep.VIDEO,
                status=StepStatus.SUCCEEDED,
                created_at=now,
                updated_at=now,
            )
        }
    )
    provider = _CountingMockVideoProvider()
    wrapped = with_step_tracking(WorkflowStep.VIDEO, repository)(nodes.make_render_video_node(provider))

    result = await wrapped(state)

    assert provider.calls == 0
    assert result["version"].video_artifact == version.video_artifact
    assert repository.save_calls == []


async def _state_with_full_review_package() -> GraphState:
    state = await _state_with_images_and_voice()
    render_node = nodes.make_render_video_node(MockVideoProvider())
    rendered = await render_node(state)
    return {"version": rendered["version"], "voice_artifact": state["voice_artifact"]}


@pytest.mark.asyncio
async def test_validate_review_package_passes_when_all_artifacts_present():
    state = await _state_with_full_review_package()

    result = await nodes.validate_review_package(state)

    validation = result["review_validation"]
    assert validation.is_valid is True
    assert validation.missing_artifacts == []


@pytest.mark.asyncio
async def test_validate_review_package_reports_all_missing_artifacts_for_a_fresh_version():
    state: GraphState = {"version": _version()}

    result = await nodes.validate_review_package(state)

    validation = result["review_validation"]
    assert validation.is_valid is False
    assert validation.missing_artifacts == [
        "strategy",
        "campaign_copy",
        "storyboard",
        "image_artifacts",
        "voice_artifact",
        "video_artifact",
    ]


@pytest.mark.asyncio
async def test_validate_review_package_reports_only_missing_voice_artifact():
    state = await _state_with_full_review_package()
    state = {"version": state["version"]}  # voice_artifact intentionally dropped

    result = await nodes.validate_review_package(state)

    validation = result["review_validation"]
    assert validation.is_valid is False
    assert validation.missing_artifacts == ["voice_artifact"]


@pytest.mark.asyncio
async def test_validate_review_package_reports_only_missing_video_artifact():
    state = await _state_with_images_and_voice()  # no render_video run yet

    result = await nodes.validate_review_package(state)

    validation = result["review_validation"]
    assert validation.is_valid is False
    assert validation.missing_artifacts == ["video_artifact"]


@pytest.mark.asyncio
async def test_validate_review_package_preserves_existing_state_keys():
    state = await _state_with_full_review_package()

    result = await nodes.validate_review_package(state)

    assert result["version"] is state["version"]
    assert result["voice_artifact"] is state["voice_artifact"]


async def _state_with_valid_review_package() -> GraphState:
    state = await _state_with_full_review_package()
    return await nodes.validate_review_package(state)


@pytest.mark.asyncio
async def test_await_human_approval_requires_prior_validate_review_package():
    state = await _state_with_full_review_package()
    with pytest.raises(ValueError, match="validate_review_package"):
        await nodes.await_human_approval(state)


@pytest.mark.asyncio
async def test_await_human_approval_rejects_an_incomplete_review_package():
    state: GraphState = {"version": _version()}
    validated = await nodes.validate_review_package(state)
    with pytest.raises(ValueError, match="incomplete"):
        await nodes.await_human_approval(validated)


@pytest.mark.asyncio
async def test_await_human_approval_sets_status_ready_for_review():
    state = await _state_with_valid_review_package()

    result = await nodes.await_human_approval(state)

    assert result["version"].status == CampaignStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_await_human_approval_preserves_all_other_version_fields():
    state = await _state_with_valid_review_package()

    result = await nodes.await_human_approval(state)

    before = state["version"].model_dump(exclude={"status"})
    after = result["version"].model_dump(exclude={"status"})
    assert before == after


async def _final_version() -> CampaignVersion:
    state = await _state_with_images_and_voice()
    render_node = nodes.make_render_video_node(MockVideoProvider())
    rendered = await render_node(state)
    return rendered["version"]


@pytest.mark.asyncio
async def test_prepare_final_package_requires_strategy_copy_and_storyboard():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="strategy"):
        await nodes.prepare_final_package(state)


@pytest.mark.asyncio
async def test_prepare_final_package_requires_prior_generate_images():
    version = await _version_with_storyboard()
    with pytest.raises(ValueError, match="generate_images"):
        await nodes.prepare_final_package({"version": version})


@pytest.mark.asyncio
async def test_prepare_final_package_requires_prior_render_video():
    version = await _version_with_storyboard()
    images_result = await nodes.make_generate_images_node(MockImageProvider())({"version": version})
    with pytest.raises(ValueError, match="render_video"):
        await nodes.prepare_final_package({"version": images_result["version"]})


@pytest.mark.asyncio
async def test_prepare_final_package_sets_status_final():
    version = await _final_version()

    result = await nodes.prepare_final_package({"version": version})

    assert result["version"].status == CampaignStatus.FINAL


@pytest.mark.asyncio
async def test_prepare_final_package_builds_review_package_with_all_artifact_ids():
    version = await _final_version()

    result = await nodes.prepare_final_package({"version": version})

    review_package = result["version"].review_package
    assert review_package is not None
    expected_ids = {artifact.artifact_id for artifact in version.image_artifacts}
    expected_ids.add(version.video_artifact.artifact_id)
    assert set(review_package.artifact_ids) == expected_ids


@pytest.mark.asyncio
async def test_prepare_final_package_manifest_checksum_is_deterministic():
    version = await _final_version()

    first = await nodes.prepare_final_package({"version": version})
    second = await nodes.prepare_final_package({"version": version})

    assert first["version"].review_package.manifest_checksum == second["version"].review_package.manifest_checksum


@pytest.mark.asyncio
async def test_prepare_final_package_does_not_regenerate_any_artifacts():
    version = await _final_version()

    result = await nodes.prepare_final_package({"version": version})

    assert result["version"].image_artifacts == version.image_artifacts
    assert result["version"].video_artifact == version.video_artifact


@pytest.mark.asyncio
async def test_await_human_approval_never_loops_sleeps_or_polls():
    source = inspect.getsource(nodes.await_human_approval)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.While, ast.For, ast.AsyncFor)), (
            f"await_human_approval must not loop/poll/retry, found {type(node).__name__}"
        )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "sleep", "await_human_approval must not busy-wait"


@pytest.mark.asyncio
async def test_handle_failure_transitions_to_failed_for_a_generic_error():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    assert result["version"].status == CampaignStatus.FAILED


@pytest.mark.asyncio
async def test_handle_failure_transitions_to_cancelled_for_node_cancelled():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, NodeCancelled("video"), step=WorkflowStep.VIDEO)

    assert result["version"].status == CampaignStatus.CANCELLED


@pytest.mark.asyncio
async def test_handle_failure_marks_retryable_when_budget_remains():
    version = await _final_version()
    version = version.model_copy(update={"retry": RetryMetadata(attempt=0, max_attempts=3)})
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("transient"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.attempt == 1
    assert result["version"].retry.retryable is True
    assert result["version"].error.code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_handle_failure_marks_not_retryable_when_budget_exhausted():
    version = await _final_version()
    version = version.model_copy(update={"retry": RetryMetadata(attempt=2, max_attempts=3)})
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("still failing"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.attempt == 3
    assert result["version"].retry.retryable is False
    assert result["version"].error.code == "RETRY_EXHAUSTED"


@pytest.mark.asyncio
async def test_handle_failure_cancellation_is_never_retryable_regardless_of_budget():
    version = await _final_version()
    version = version.model_copy(update={"retry": RetryMetadata(attempt=0, max_attempts=3)})
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, NodeCancelled("video"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.retryable is False
    assert result["version"].retry.attempt == 0
    assert result["version"].error.code == "CANCELLED_BY_USER"


@pytest.mark.asyncio
async def test_handle_failure_records_resume_step():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.resume_step == WorkflowStep.VIDEO
    assert result["version"].error.workflow_step == WorkflowStep.VIDEO


@pytest.mark.asyncio
async def test_handle_failure_records_a_sanitized_error_with_the_message_and_component():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    error = result["version"].error
    assert error is not None
    assert error.message == "boom"
    assert error.component == ErrorComponent.LANGGRAPH_WORKER
    assert error.campaign_id == version.campaign_id
    assert error.campaign_version == version.campaign_version


@pytest.mark.asyncio
async def test_handle_failure_preserves_all_existing_content():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    updated = result["version"]
    assert updated.strategy == version.strategy
    assert updated.campaign_copy == version.campaign_copy
    assert updated.storyboard == version.storyboard
    assert updated.image_artifacts == version.image_artifacts
    assert updated.video_artifact == version.video_artifact


@pytest.mark.asyncio
async def test_handle_failure_never_loops_sleeps_or_retries():
    source = inspect.getsource(nodes.handle_failure)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.While, ast.For, ast.AsyncFor)), (
            f"handle_failure must not loop/poll/retry, found {type(node).__name__}"
        )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "sleep", "handle_failure must not busy-wait"
