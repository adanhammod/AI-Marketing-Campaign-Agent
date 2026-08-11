from typing import Any

import httpx

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.images.models import PhotoCandidate


class PexelsPhotoClient:
    _SEARCH_URL = "https://api.pexels.com/v1/search"

    def __init__(self, credential: str, client: httpx.AsyncClient, *, per_page: int = 15) -> None:
        if not credential:
            raise ValueError("Pexels credential is required")
        if not 1 <= per_page <= 40:
            raise ValueError("Pexels candidate count must be between 1 and 40")
        self._credential = credential
        self._client = client
        self._per_page = per_page

    async def search(self, query: str) -> list[PhotoCandidate]:
        try:
            response = await self._client.get(
                self._SEARCH_URL,
                headers={"Authorization": self._credential},
                params={"query": query, "orientation": "portrait", "per_page": self._per_page},
            )
        except httpx.TimeoutException as exc:
            raise WorkflowOperationError("PROVIDER_TIMEOUT", "Pexels search timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Pexels search unavailable", retryable=True
            ) from exc
        if response.status_code == 429:
            raise WorkflowOperationError("PROVIDER_THROTTLED", "Pexels search throttled", retryable=True)
        if response.status_code in (401, 403):
            raise WorkflowOperationError("IMAGE_PROVIDER_UNAVAILABLE", "Pexels authentication failed", retryable=False)
        if response.status_code >= 500:
            raise WorkflowOperationError("IMAGE_PROVIDER_UNAVAILABLE", "Pexels search unavailable", retryable=True)
        if response.status_code >= 400:
            raise WorkflowOperationError("INVALID_PROVIDER_OUTPUT", "Pexels rejected the search", retryable=False)
        try:
            raw_photos: list[dict[str, Any]] = response.json()["photos"]
            return [self._parse_photo(photo) for photo in raw_photos]
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Pexels returned malformed photo data", retryable=False
            ) from exc

    async def download(self, photo: PhotoCandidate, max_bytes: int) -> bytes:
        try:
            async with self._client.stream("GET", photo.source_url) as response:
                if response.status_code >= 500:
                    raise WorkflowOperationError(
                        "IMAGE_PROVIDER_UNAVAILABLE", "Pexels image download unavailable", retryable=True
                    )
                if response.status_code >= 400:
                    raise WorkflowOperationError(
                        "INVALID_PROVIDER_OUTPUT", "Pexels image download rejected", retryable=False
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise WorkflowOperationError(
                            "ARTIFACT_VALIDATION_FAILED", "image exceeds size limit", retryable=False
                        )
                return bytes(content)
        except WorkflowOperationError:
            raise
        except httpx.TimeoutException as exc:
            raise WorkflowOperationError("PROVIDER_TIMEOUT", "Pexels image download timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Pexels image download unavailable", retryable=True
            ) from exc

    @staticmethod
    def _parse_photo(photo: dict[str, Any]) -> PhotoCandidate:
        source = photo["src"]
        source_url = str(source.get("portrait") or source["original"])
        return PhotoCandidate(
            photo_id=int(photo["id"]),
            width=int(photo["width"]),
            height=int(photo["height"]),
            source_url=source_url,
            photo_page_url=str(photo.get("url", "")),
            photographer=str(photo.get("photographer", "")),
            photographer_id=int(photo["photographer_id"]) if photo.get("photographer_id") is not None else None,
            photographer_url=str(photo.get("photographer_url", "")),
            alt_text=str(photo.get("alt", "")),
            source_variant="portrait" if source.get("portrait") else "original",
        )
