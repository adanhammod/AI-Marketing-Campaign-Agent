import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.artifacts import VideoArtifactReference
from campaign_contracts.enums import WorkflowStep

from .base import VideoProvider
from .models import VideoRenderRequest, VideoRenderResult


class MockVideoProvider(VideoProvider):
    """Deterministic, clearly-synthetic mock. Never disclosed as a real generated asset."""

    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult:
        started_at = datetime.now(UTC)
        scene_signature = "|".join(f"{scene.scene_number}:{scene.visual_prompt}" for scene in request.storyboard.scenes)
        image_signature = "|".join(str(artifact.artifact_id) for artifact in request.image_artifacts)
        signature = f"{request.campaign_id}:{request.campaign_version}:{scene_signature}:{image_signature}"
        checksum = hashlib.sha256(signature.encode()).hexdigest()
        artifact = VideoArtifactReference(
            artifact_id=uuid4(),
            campaign_id=request.campaign_id,
            campaign_version=request.campaign_version,
            workflow_step=WorkflowStep.VIDEO,
            mime_type="video/mp4",
            size_bytes=2_000_000,
            checksum_sha256=checksum,
            created_at=started_at,
            provider="mock-video-provider",
        )
        completed_at = datetime.now(UTC)
        return VideoRenderResult(
            provider="mock-video-provider",
            fallback_asset=False,
            started_at=started_at,
            completed_at=completed_at,
            artifact=artifact,
        )
