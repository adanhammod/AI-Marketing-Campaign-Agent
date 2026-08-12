import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.artifacts import FinalPackageArtifactReference
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep


class MockPackagePipeline:
    """Deterministic, clearly-synthetic mock. Never disclosed as a real generated asset."""

    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> FinalPackageArtifactReference:
        signature = f"{version.campaign_id}:{version.campaign_version}:mock-package"
        checksum = hashlib.sha256(signature.encode()).hexdigest()
        return FinalPackageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.PACKAGE,
            mime_type="application/zip",
            size_bytes=2_000_000,
            checksum_sha256=checksum,
            created_at=datetime.now(UTC),
            provider="mock-package-pipeline",
        )
