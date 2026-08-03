import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.artifacts import ImageArtifactReference
from campaign_contracts.enums import WorkflowStep

from .base import ImageProvider
from .models import ImageGenerationRequest, ImageGenerationResult


class MockImageProvider(ImageProvider):
    """Deterministic, clearly-synthetic mock. Never disclosed as a real generated asset."""

    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        started_at = datetime.now(UTC)
        signature = (
            f"{request.campaign_id}:{request.campaign_version}:{request.prompt.scene_number}:{request.prompt.prompt}"
        )
        checksum = hashlib.sha256(signature.encode()).hexdigest()
        artifact = ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=request.campaign_id,
            campaign_version=request.campaign_version,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/png",
            size_bytes=1024,
            checksum_sha256=checksum,
            created_at=started_at,
            provider="mock-image-provider",
        )
        completed_at = datetime.now(UTC)
        return ImageGenerationResult(
            provider="mock-image-provider",
            fallback_asset=False,
            started_at=started_at,
            completed_at=completed_at,
            artifact=artifact,
        )
