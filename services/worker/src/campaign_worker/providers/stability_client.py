from dataclasses import dataclass

import httpx

from campaign_worker.errors import WorkflowOperationError


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    data: bytes
    content_type: str
    seed: int | None
    finish_reason: str | None


class StabilityImageClient:
    """Text-to-image client for Stability AI's v2beta stable-image/generate/sd3 endpoint.

    Moderation-filtered generations come back as HTTP 200 with a
    ``finish-reason: CONTENT_FILTERED`` response header (not a distinct
    status code) -- SD3.5's safety classifier runs post-generation, unlike
    some older Stability endpoints that reject the request outright.
    """

    _GENERATE_URL = "https://api.stability.ai/v2beta/stable-image/generate/sd3"
    _AUTH_SCHEME = "Bearer"

    def __init__(
        self,
        credential: str,
        client: httpx.AsyncClient,
        *,
        model: str,
        aspect_ratio: str,
        output_format: str = "jpeg",
        max_download_bytes: int = 25_000_000,
    ) -> None:
        if not credential:
            raise ValueError("Stability credential is required")
        if not model:
            raise ValueError("Stability model is required")
        if not aspect_ratio:
            raise ValueError("Stability aspect ratio is required")
        self._credential = credential
        self._client = client
        self._model = model
        self._aspect_ratio = aspect_ratio
        self._output_format = output_format
        self._max_download_bytes = max_download_bytes

    async def generate(self, positive_prompt: str, negative_prompt: str) -> GeneratedImage:
        try:
            response = await self._client.post(
                self._GENERATE_URL,
                headers={"Authorization": f"{self._AUTH_SCHEME} {self._credential}", "Accept": "image/*"},
                data={
                    "prompt": positive_prompt,
                    "negative_prompt": negative_prompt,
                    "model": self._model,
                    "aspect_ratio": self._aspect_ratio,
                    "output_format": self._output_format,
                    "mode": "text-to-image",
                },
                # Stability's API requires multipart/form-data even for a
                # request with no file fields; a dummy files= entry forces
                # httpx to encode the request that way (mirrors Stability's
                # own published client examples).
                files={"none": (None, b"")},
            )
        except httpx.TimeoutException as exc:
            raise WorkflowOperationError("PROVIDER_TIMEOUT", "Stability generation timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Stability generation unavailable", retryable=True
            ) from exc

        if response.status_code == 429:
            raise WorkflowOperationError("PROVIDER_THROTTLED", "Stability generation throttled", retryable=True)
        if response.status_code == 402:
            raise WorkflowOperationError(
                "PROVIDER_THROTTLED", "Stability account has insufficient credits", retryable=False
            )
        if response.status_code in (401, 403):
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Stability authentication failed", retryable=False
            )
        if response.status_code >= 500:
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Stability generation unavailable", retryable=True
            )
        if response.status_code >= 400:
            raise WorkflowOperationError("INVALID_PROVIDER_OUTPUT", "Stability rejected the request", retryable=False)

        finish_reason = response.headers.get("finish-reason")
        if finish_reason and finish_reason != "SUCCESS":
            raise WorkflowOperationError(
                "PROVIDER_POLICY_REJECTION",
                "Stability rejected the prompt for content policy reasons",
                retryable=False,
            )

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Stability response was not an image", retryable=False
            )
        data = response.content
        if not data:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Stability returned an empty image", retryable=False
            )
        if len(data) > self._max_download_bytes:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED", "generated image exceeds size limit", retryable=False
            )

        seed_header = response.headers.get("seed")
        seed = int(seed_header) if seed_header is not None and seed_header.isdigit() else None
        return GeneratedImage(data=data, content_type=content_type, seed=seed, finish_reason=finish_reason)
