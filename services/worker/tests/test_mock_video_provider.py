import ast
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from campaign_contracts.artifacts import ImageArtifactReference
from campaign_contracts.campaign import Storyboard, StoryboardScene
from campaign_contracts.enums import ArtifactType

from campaign_worker.providers.base import VideoProvider
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.models import VideoRenderRequest


def _storyboard(prompts: tuple[str, str, str] = ("scene one", "scene two", "scene three")) -> Storyboard:
    scenes = [
        StoryboardScene(
            scene_number=i,
            purpose="p",
            duration_seconds=5,
            narration="n",
            visual_prompt=prompts[i - 1],
            transition="cut",
        )
        for i in (1, 2, 3)
    ]
    return Storyboard(scenes=scenes, total_duration_seconds=15)


def _image_artifact(campaign_id, campaign_version=1) -> ImageArtifactReference:
    now = datetime.now(UTC)
    return ImageArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        workflow_step="images",
        mime_type="image/png",
        size_bytes=1024,
        checksum_sha256="a" * 64,
        created_at=now,
        provider="mock-image-provider",
    )


def _request(**overrides):
    campaign_id = overrides.pop("campaign_id", uuid4())
    campaign_version = overrides.pop("campaign_version", 1)
    storyboard = overrides.pop("storyboard", _storyboard())
    image_artifacts = overrides.pop("image_artifacts", [])
    return VideoRenderRequest(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        storyboard=storyboard,
        image_artifacts=image_artifacts,
    )


@pytest.mark.asyncio
async def test_mock_video_provider_satisfies_the_video_provider_interface():
    provider = MockVideoProvider()
    assert isinstance(provider, VideoProvider)


@pytest.mark.asyncio
async def test_mock_video_provider_produces_valid_result():
    provider = MockVideoProvider()
    result = await provider.render_video(_request())

    assert result.error is None
    assert result.fallback_asset is False
    assert result.provider == "mock-video-provider"
    assert result.artifact is not None
    assert result.artifact.artifact_type == ArtifactType.VIDEO
    assert result.artifact.mime_type == "video/mp4"
    assert len(result.artifact.checksum_sha256) == 64
    assert result.started_at <= result.completed_at


@pytest.mark.asyncio
async def test_mock_video_provider_never_calls_external_services():
    path = Path(__file__).parents[1] / "src" / "campaign_worker" / "providers" / "mock_video_provider.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in ("mcp", "httpx", "boto3", "subprocess", "urllib", "socket"):
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported)


@pytest.mark.asyncio
async def test_mock_video_provider_is_deterministic_across_repeated_calls():
    provider = MockVideoProvider()
    request = _request()

    checksums = [(await provider.render_video(request)).artifact.checksum_sha256 for _ in range(5)]

    assert len(set(checksums)) == 1, f"expected identical checksums across 5 calls, got {checksums}"


@pytest.mark.asyncio
async def test_mock_video_provider_assigns_a_fresh_artifact_id_per_call_even_when_content_is_identical():
    provider = MockVideoProvider()
    request = _request()

    first = await provider.render_video(request)
    second = await provider.render_video(request)

    assert first.artifact.checksum_sha256 == second.artifact.checksum_sha256
    assert first.artifact.artifact_id != second.artifact.artifact_id


@pytest.mark.asyncio
async def test_mock_video_provider_produces_different_checksum_for_different_storyboard_content():
    provider = MockVideoProvider()
    first = await provider.render_video(_request(storyboard=_storyboard(("a", "b", "c"))))
    second = await provider.render_video(_request(storyboard=_storyboard(("x", "b", "c"))))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_video_provider_produces_different_checksum_for_different_campaign():
    provider = MockVideoProvider()
    first = await provider.render_video(_request(campaign_id=uuid4()))
    second = await provider.render_video(_request(campaign_id=uuid4()))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_video_provider_produces_different_checksum_for_different_campaign_version():
    provider = MockVideoProvider()
    campaign_id = uuid4()
    first = await provider.render_video(_request(campaign_id=campaign_id, campaign_version=1))
    second = await provider.render_video(_request(campaign_id=campaign_id, campaign_version=2))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_video_provider_produces_different_checksum_for_different_image_artifacts():
    provider = MockVideoProvider()
    campaign_id = uuid4()
    first = await provider.render_video(
        _request(campaign_id=campaign_id, image_artifacts=[_image_artifact(campaign_id)])
    )
    second = await provider.render_video(
        _request(campaign_id=campaign_id, image_artifacts=[_image_artifact(campaign_id)])
    )
    # Each _image_artifact() call mints a distinct artifact_id, so the image signature differs.
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_video_provider_result_carries_matching_campaign_identity():
    campaign_id = uuid4()
    provider = MockVideoProvider()
    result = await provider.render_video(_request(campaign_id=campaign_id, campaign_version=3))
    assert result.artifact.campaign_id == campaign_id
    assert result.artifact.campaign_version == 3
