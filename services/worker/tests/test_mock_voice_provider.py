import ast
from pathlib import Path
from uuid import uuid4

import pytest

from campaign_worker.providers.base import VoiceProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.providers.voice_models import VoiceGenerationRequest


def _request(**overrides):
    campaign_id = overrides.pop("campaign_id", uuid4())
    campaign_version = overrides.pop("campaign_version", 1)
    narration_text = overrides.pop("narration_text", "Better mornings, delivered weekly.")
    return VoiceGenerationRequest(
        campaign_id=campaign_id, campaign_version=campaign_version, narration_text=narration_text
    )


@pytest.mark.asyncio
async def test_mock_voice_provider_satisfies_the_interface():
    assert isinstance(MockVoiceProvider(), VoiceProvider)


@pytest.mark.asyncio
async def test_mock_voice_provider_produces_valid_result():
    result = await MockVoiceProvider().generate_voice(_request())
    assert result.error is None
    assert result.provider == "mock-voice-provider"
    assert result.artifact is not None
    assert result.artifact.mime_type == "audio/mpeg"
    assert len(result.artifact.checksum_sha256) == 64
    assert result.started_at <= result.completed_at


@pytest.mark.asyncio
async def test_mock_voice_provider_never_calls_external_services():
    path = Path(__file__).parents[1] / "src" / "campaign_worker" / "providers" / "mock_voice_provider.py"
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
async def test_mock_voice_provider_is_deterministic_across_repeated_calls():
    provider = MockVoiceProvider()
    request = _request()
    checksums = [(await provider.generate_voice(request)).artifact.checksum_sha256 for _ in range(5)]
    assert len(set(checksums)) == 1, f"expected identical checksums across 5 calls, got {checksums}"


@pytest.mark.asyncio
async def test_mock_voice_provider_assigns_fresh_artifact_id_per_call():
    provider = MockVoiceProvider()
    request = _request()
    first = await provider.generate_voice(request)
    second = await provider.generate_voice(request)
    assert first.artifact.checksum_sha256 == second.artifact.checksum_sha256
    assert first.artifact.artifact_id != second.artifact.artifact_id


@pytest.mark.asyncio
async def test_mock_voice_provider_produces_different_checksum_for_different_narration():
    provider = MockVoiceProvider()
    first = await provider.generate_voice(_request(narration_text="one"))
    second = await provider.generate_voice(_request(narration_text="two"))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_voice_provider_produces_different_checksum_for_different_campaign():
    provider = MockVoiceProvider()
    first = await provider.generate_voice(_request(campaign_id=uuid4()))
    second = await provider.generate_voice(_request(campaign_id=uuid4()))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256


@pytest.mark.asyncio
async def test_mock_voice_provider_produces_different_checksum_for_different_campaign_version():
    provider = MockVoiceProvider()
    campaign_id = uuid4()
    first = await provider.generate_voice(_request(campaign_id=campaign_id, campaign_version=1))
    second = await provider.generate_voice(_request(campaign_id=campaign_id, campaign_version=2))
    assert first.artifact.checksum_sha256 != second.artifact.checksum_sha256
