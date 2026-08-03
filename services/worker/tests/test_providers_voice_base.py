import inspect
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.errors import SanitizedWorkflowError

from campaign_worker.providers.base import VoiceProvider
from campaign_worker.providers.voice_models import VoiceGenerationRequest, VoiceGenerationResult


def test_voice_provider_is_abstract_with_generate_voice():
    assert inspect.isabstract(VoiceProvider)
    assert "generate_voice" in VoiceProvider.__abstractmethods__


class _AlwaysSucceedsVoiceProvider(VoiceProvider):
    async def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        now = datetime.now(UTC)
        return VoiceGenerationResult(provider="always-succeeds", fallback_asset=False, started_at=now, completed_at=now)


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


async def _run_provider(provider: VoiceProvider, request: VoiceGenerationRequest) -> VoiceGenerationResult:
    return await provider.generate_voice(request)


@pytest.mark.asyncio
async def test_two_different_voice_provider_implementations_are_substitutable_through_the_same_interface():
    request = VoiceGenerationRequest(campaign_id=uuid4(), campaign_version=1, narration_text="a narration")

    succeeded = await _run_provider(_AlwaysSucceedsVoiceProvider(), request)
    failed = await _run_provider(_AlwaysFailsVoiceProvider(), request)

    assert isinstance(succeeded, VoiceGenerationResult) and succeeded.error is None
    assert isinstance(failed, VoiceGenerationResult) and failed.error is not None
