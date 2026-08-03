import ast
from pathlib import Path
from uuid import uuid4

import pytest
from campaign_contracts.campaign import ImagePrompt
from campaign_contracts.enums import ArtifactType

from campaign_worker.providers.base import ImageProvider
from campaign_worker.providers.mock_image_provider import MockImageProvider
from campaign_worker.providers.models import ImageGenerationRequest


def _request(**overrides):
    campaign_id = overrides.pop("campaign_id", uuid4())
    campaign_version = overrides.pop("campaign_version", 1)
    prompt_overrides = overrides.pop("prompt_overrides", {})
    prompt_defaults = dict(scene_number=1, prompt="a cup of cold brew coffee, scene 1", aspect_ratio="9:16")
    prompt_defaults.update(prompt_overrides)
    return ImageGenerationRequest(
        campaign_id=campaign_id, campaign_version=campaign_version, prompt=ImagePrompt(**prompt_defaults)
    )


@pytest.mark.asyncio
async def test_mock_image_provider_satisfies_the_image_provider_interface():
    provider = MockImageProvider()
    assert isinstance(provider, ImageProvider)


@pytest.mark.asyncio
async def test_mock_image_provider_produces_valid_result():
    provider = MockImageProvider()
    result = await provider.generate_image(_request())

    assert result.error is None
    assert result.fallback_asset is False
    assert result.provider == "mock-image-provider"
    assert result.artifact is not None
    assert result.artifact.artifact_type == ArtifactType.IMAGE
    assert result.artifact.mime_type == "image/png"
    assert len(result.artifact.checksum_sha256) == 64
    assert result.started_at <= result.completed_at


@pytest.mark.asyncio
async def test_mock_image_provider_never_calls_external_services():
    # Structural proof, not just a claim: parse the module's own imports.
    path = Path(__file__).parents[1] / "src" / "campaign_worker" / "providers" / "mock_image_provider.py"
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
async def test_mock_image_provider_is_deterministic_across_repeated_calls():
    provider = MockImageProvider()
    request = _request()

    checksums = [(await provider.generate_image(request)).artifact.checksum_sha256 for _ in range(5)]

    assert len(set(checksums)) == 1, f"expected identical checksums across 5 calls, got {checksums}"


@pytest.mark.asyncio
async def test_mock_image_provider_assigns_a_fresh_artifact_id_per_call_even_when_content_is_identical():
    provider = MockImageProvider()
    request = _request()

    first = await provider.generate_image(request)
    second = await provider.generate_image(request)

    assert first.artifact.checksum_sha256 == second.artifact.checksum_sha256
    assert first.artifact.artifact_id != second.artifact.artifact_id


@pytest.mark.asyncio
async def test_mock_image_provider_produces_different_checksum_for_different_prompt_text():
    provider = MockImageProvider()
    first = await provider.generate_image(_request(prompt_overrides={"prompt": "scene one"}))
    second = await provider.generate_image(_request(prompt_overrides={"prompt": "scene two"}))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_image_provider_produces_different_checksum_for_different_scene_number():
    provider = MockImageProvider()
    first = await provider.generate_image(_request(prompt_overrides={"scene_number": 1}))
    second = await provider.generate_image(_request(prompt_overrides={"scene_number": 2}))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_image_provider_produces_different_checksum_for_different_campaign():
    provider = MockImageProvider()
    first = await provider.generate_image(_request(campaign_id=uuid4()))
    second = await provider.generate_image(_request(campaign_id=uuid4()))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_image_provider_produces_different_checksum_for_different_campaign_version():
    provider = MockImageProvider()
    campaign_id = uuid4()
    first = await provider.generate_image(_request(campaign_id=campaign_id, campaign_version=1))
    second = await provider.generate_image(_request(campaign_id=campaign_id, campaign_version=2))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_image_provider_result_carries_matching_campaign_identity():
    campaign_id = uuid4()
    provider = MockImageProvider()
    result = await provider.generate_image(_request(campaign_id=campaign_id, campaign_version=3))
    assert result.artifact.campaign_id == campaign_id
    assert result.artifact.campaign_version == 3
