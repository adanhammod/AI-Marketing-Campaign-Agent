import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.artifacts import AudioArtifactReference
from campaign_contracts.enums import WorkflowStep

from .base import VoiceProvider
from .voice_models import VoiceGenerationRequest, VoiceGenerationResult


class MockVoiceProvider(VoiceProvider):
    """Deterministic, clearly-synthetic mock. Never disclosed as a real generated asset."""

    async def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        started_at = datetime.now(UTC)
        signature = f"{request.campaign_id}:{request.campaign_version}:{request.narration_text}"
        checksum = hashlib.sha256(signature.encode()).hexdigest()
        artifact = AudioArtifactReference(
            artifact_id=uuid4(),
            campaign_id=request.campaign_id,
            campaign_version=request.campaign_version,
            workflow_step=WorkflowStep.VOICEOVER,
            mime_type="audio/mpeg",
            size_bytes=2048,
            checksum_sha256=checksum,
            created_at=started_at,
            provider="mock-voice-provider",
        )
        completed_at = datetime.now(UTC)
        return VoiceGenerationResult(
            provider="mock-voice-provider",
            fallback_asset=False,
            started_at=started_at,
            completed_at=completed_at,
            artifact=artifact,
        )
