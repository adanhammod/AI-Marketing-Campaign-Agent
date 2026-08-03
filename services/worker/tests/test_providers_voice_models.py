from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from campaign_worker.providers.voice_models import VoiceGenerationRequest, VoiceGenerationResult


def test_voice_generation_request_accepts_valid_fields():
    request = VoiceGenerationRequest(campaign_id=uuid4(), campaign_version=1, narration_text="Better mornings.")
    assert request.campaign_version == 1


def test_voice_generation_request_rejects_missing_narration_text():
    with pytest.raises(ValidationError):
        VoiceGenerationRequest(campaign_id=uuid4(), campaign_version=1)


def test_voice_generation_request_rejects_blank_narration_text():
    with pytest.raises(ValidationError):
        VoiceGenerationRequest(campaign_id=uuid4(), campaign_version=1, narration_text="")


def test_voice_generation_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        VoiceGenerationRequest(campaign_id=uuid4(), campaign_version=1, narration_text="x", unexpected="nope")


def test_voice_generation_result_accepts_successful_artifact():
    from campaign_contracts.artifacts import PublicArtifactReference
    from campaign_contracts.enums import ArtifactType, WorkflowStep

    now = datetime.now(UTC)
    artifact = PublicArtifactReference(
        artifact_id=uuid4(),
        artifact_type=ArtifactType.AUDIO,
        campaign_id=uuid4(),
        campaign_version=1,
        workflow_step=WorkflowStep.VIDEO,
        mime_type="audio/mpeg",
        size_bytes=2048,
        checksum_sha256="c" * 64,
        created_at=now,
        provider="mock-voice-provider",
    )
    result = VoiceGenerationResult(
        provider="mock-voice-provider", fallback_asset=False, started_at=now, completed_at=now, artifact=artifact
    )
    assert result.error is None


def test_voice_generation_result_accepts_sanitized_error():
    from campaign_contracts.errors import SanitizedWorkflowError

    now = datetime.now(UTC)
    error = SanitizedWorkflowError(
        code="INTERNAL_ERROR",
        message="Voice provider did not respond.",
        component="UNKNOWN",
        attempt=1,
        retryable=True,
        timestamp=now,
        correlation_id=uuid4(),
    )
    result = VoiceGenerationResult(provider="mock", fallback_asset=False, started_at=now, completed_at=now, error=error)
    assert result.artifact is None


def test_voice_generation_result_rejects_unknown_field():
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        VoiceGenerationResult(
            provider="mock", fallback_asset=False, started_at=now, completed_at=now, unexpected="nope"
        )
