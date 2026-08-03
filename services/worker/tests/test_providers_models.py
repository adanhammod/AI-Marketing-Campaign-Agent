from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.campaign import ImagePrompt, Storyboard, StoryboardScene
from campaign_contracts.errors import SanitizedWorkflowError
from pydantic import ValidationError

from campaign_worker.providers.models import (
    ImageGenerationRequest,
    ImageGenerationResult,
    VideoRenderRequest,
    VideoRenderResult,
)


def _prompt(**overrides):
    defaults = dict(scene_number=1, prompt="a cup of cold brew coffee, scene 1", aspect_ratio="9:16")
    defaults.update(overrides)
    return ImagePrompt(**defaults)


def _storyboard():
    scenes = [
        StoryboardScene(
            scene_number=i, purpose="p", duration_seconds=5, narration="n", visual_prompt="v", transition="cut"
        )
        for i in (1, 2, 3)
    ]
    return Storyboard(scenes=scenes, total_duration_seconds=15)


def test_image_generation_request_accepts_valid_fields():
    request = ImageGenerationRequest(campaign_id=uuid4(), campaign_version=1, prompt=_prompt())
    assert request.campaign_version == 1


def test_image_generation_request_rejects_missing_prompt():
    with pytest.raises(ValidationError):
        ImageGenerationRequest(campaign_id=uuid4(), campaign_version=1)


def test_image_generation_request_rejects_non_9_16_aspect_ratio():
    with pytest.raises(ValidationError, match="9:16"):
        ImageGenerationRequest(campaign_id=uuid4(), campaign_version=1, prompt=_prompt(aspect_ratio="1:1"))


def test_image_generation_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        ImageGenerationRequest(campaign_id=uuid4(), campaign_version=1, prompt=_prompt(), unexpected="nope")


def test_video_render_request_accepts_valid_fields():
    request = VideoRenderRequest(campaign_id=uuid4(), campaign_version=1, storyboard=_storyboard())
    assert request.aspect_ratio == "9:16"


def test_video_render_request_rejects_missing_storyboard():
    with pytest.raises(ValidationError):
        VideoRenderRequest(campaign_id=uuid4(), campaign_version=1)


def test_video_render_request_rejects_non_9_16_aspect_ratio():
    with pytest.raises(ValidationError):
        VideoRenderRequest(campaign_id=uuid4(), campaign_version=1, storyboard=_storyboard(), aspect_ratio="16:9")


def test_image_generation_result_accepts_successful_artifact():
    from campaign_contracts.artifacts import ImageArtifactReference

    now = datetime.now(UTC)
    artifact = ImageArtifactReference(
        artifact_id=uuid4(),
        campaign_id=uuid4(),
        campaign_version=1,
        workflow_step="images",
        mime_type="image/png",
        size_bytes=1024,
        checksum_sha256="a" * 64,
        created_at=now,
        provider="mock-image-provider",
    )
    result = ImageGenerationResult(
        provider="mock-image-provider", fallback_asset=False, started_at=now, completed_at=now, artifact=artifact
    )
    assert result.error is None
    assert result.artifact is not None


def test_image_generation_result_accepts_sanitized_error_with_allowlisted_code():
    now = datetime.now(UTC)
    error = SanitizedWorkflowError(
        code="IMAGE_PROVIDER_UNAVAILABLE",
        message="Image provider did not respond in time.",
        component="IMAGE_MCP",
        attempt=1,
        retryable=True,
        timestamp=now,
        correlation_id=uuid4(),
    )
    result = ImageGenerationResult(
        provider="image-mcp", fallback_asset=False, started_at=now, completed_at=now, error=error
    )
    assert result.artifact is None
    assert result.error is not None


def test_sanitized_workflow_error_rejects_unknown_code():
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        SanitizedWorkflowError(
            code="NOT_A_REAL_CODE",
            message="x",
            component="IMAGE_MCP",
            attempt=1,
            retryable=True,
            timestamp=now,
            correlation_id=uuid4(),
        )


def test_video_render_result_accepts_fallback_disclosure():
    now = datetime.now(UTC)
    from campaign_contracts.artifacts import VideoArtifactReference

    artifact = VideoArtifactReference(
        artifact_id=uuid4(),
        campaign_id=uuid4(),
        campaign_version=1,
        workflow_step="video",
        mime_type="video/mp4",
        size_bytes=2_000_000,
        checksum_sha256="b" * 64,
        created_at=now,
        provider="hyperframes",
    )
    result = VideoRenderResult(
        provider="hyperframes", fallback_asset=True, started_at=now, completed_at=now, artifact=artifact
    )
    assert result.fallback_asset is True
