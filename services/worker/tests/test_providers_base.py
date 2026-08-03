import inspect
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.campaign import ImagePrompt

from campaign_worker.providers.base import ImageProvider, VideoProvider
from campaign_worker.providers.models import ImageGenerationRequest, ImageGenerationResult


def test_image_provider_is_abstract_with_generate_image():
    assert inspect.isabstract(ImageProvider)
    assert "generate_image" in ImageProvider.__abstractmethods__


def test_video_provider_is_abstract_with_render_video():
    assert inspect.isabstract(VideoProvider)
    assert "render_video" in VideoProvider.__abstractmethods__


class _AlwaysSucceedsProvider(ImageProvider):
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        now = datetime.now(UTC)
        return ImageGenerationResult(provider="always-succeeds", fallback_asset=False, started_at=now, completed_at=now)


class _AlwaysFailsProvider(ImageProvider):
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        now = datetime.now(UTC)
        from campaign_contracts.errors import SanitizedWorkflowError

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


async def _run_provider(provider: ImageProvider, request: ImageGenerationRequest) -> ImageGenerationResult:
    return await provider.generate_image(request)


@pytest.mark.asyncio
async def test_two_different_implementations_are_substitutable_through_the_same_interface():
    request = ImageGenerationRequest(
        campaign_id=uuid4(), campaign_version=1, prompt=ImagePrompt(scene_number=1, prompt="a scene")
    )

    succeeded = await _run_provider(_AlwaysSucceedsProvider(), request)
    failed = await _run_provider(_AlwaysFailsProvider(), request)

    assert isinstance(succeeded, ImageGenerationResult) and succeeded.error is None
    assert isinstance(failed, ImageGenerationResult) and failed.error is not None


def test_incomplete_provider_implementation_cannot_be_instantiated():
    class _Incomplete(ImageProvider):
        pass

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]
