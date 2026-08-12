import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from campaign_api.artifacts.artifact_url_signer import ArtifactURLSigner
from campaign_api.exceptions import RepositoryFailure


class S3ArtifactURLSigner(ArtifactURLSigner):
    def __init__(self, client: Any, bucket: str, expires_seconds: int) -> None:
        if not bucket:
            raise ValueError("artifact bucket is required")
        if not 1 <= expires_seconds <= 900:
            raise ValueError("presigned expiry must be between 1 and 900 seconds")
        self._client = client
        self._bucket = bucket
        self._expires_seconds = expires_seconds

    async def sign_image(self, campaign_id: UUID, campaign_version: int, scene_number: int) -> tuple[str, datetime]:
        if campaign_version < 1 or not 1 <= scene_number <= 3:
            raise ValueError("invalid artifact identity")
        key = f"campaigns/{campaign_id}/versions/{campaign_version}/images/scene-{scene_number}.jpg"
        return await self._sign(key)

    async def sign_audio(self, campaign_id: UUID, campaign_version: int) -> tuple[str, datetime]:
        if campaign_version < 1:
            raise ValueError("invalid artifact identity")
        key = f"campaigns/{campaign_id}/versions/{campaign_version}/audio/voiceover.mp3"
        return await self._sign(key)

    async def _sign(self, key: str) -> tuple[str, datetime]:
        try:
            url = await asyncio.to_thread(
                self._client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=self._expires_seconds,
            )
        except Exception:
            raise RepositoryFailure("artifact access temporarily unavailable") from None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise RepositoryFailure("artifact access temporarily unavailable")
        return url, datetime.now(UTC) + timedelta(seconds=self._expires_seconds)
