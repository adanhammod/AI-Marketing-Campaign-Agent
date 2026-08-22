import base64
import binascii

import httpx

from campaign_worker.errors import WorkflowOperationError

from .stability_client import GeneratedImage

# GeneratedImage is provider-agnostic (data/content_type/seed/finish_reason)
# and already lives in stability_client.py; reused here rather than
# duplicated so both providers return the exact same shape to
# GenerativeImagePipeline.


class CloudflareFluxClient:
    """Text-to-image client for Cloudflare Workers AI's FLUX text-to-image models.

    Cloudflare's documented response for
    ``@cf/black-forest-labs/flux-1-schnell`` is always a JSON envelope::

        {"result": {"image": "<base64>"}, "success": true, "errors": [], "messages": []}

    where ``result.image`` is a base64-encoded JPEG. There is no documented
    raw-bytes/Accept-header response mode for this model (unlike Stability's
    ``Accept: image/*`` -> raw-bytes endpoint) -- the client always parses
    this JSON shape and base64-decodes the image field itself.

    ``@cf/black-forest-labs/flux-1-schnell`` has no negative-prompt or
    aspect-ratio request parameters, unlike Stability's SD3 endpoint --
    ``negative_prompt`` is accepted (to satisfy the shared
    ``GenerativeImageClient`` protocol) but intentionally unused, and there
    is no distinct moderation-rejection signal: a rejected/invalid prompt
    comes back as a generic 400.
    """

    _AUTH_SCHEME = "Bearer"
    # Cloudflare's documented example shows the base64 payload as JPEG; there
    # is no header/field in the response that states the format explicitly.
    _CONTENT_TYPE = "image/jpeg"

    def __init__(
        self,
        account_id: str,
        api_token: str,
        client: httpx.AsyncClient,
        *,
        model: str,
        steps: int = 4,
        max_download_bytes: int = 25_000_000,
    ) -> None:
        if not account_id:
            raise ValueError("Cloudflare account ID is required")
        if not api_token:
            raise ValueError("Cloudflare API token is required")
        if not model:
            raise ValueError("Cloudflare Workers AI model is required")
        self._account_id = account_id
        self._api_token = api_token
        self._client = client
        self._model = model
        self._steps = steps
        self._max_download_bytes = max_download_bytes

    @property
    def _generate_url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run/{self._model}"

    async def generate(self, positive_prompt: str, negative_prompt: str) -> GeneratedImage:
        del negative_prompt  # flux-1-schnell has no negative-prompt parameter.
        try:
            response = await self._client.post(
                self._generate_url,
                headers={"Authorization": f"{self._AUTH_SCHEME} {self._api_token}"},
                json={"prompt": positive_prompt, "steps": self._steps},
            )
        except httpx.TimeoutException as exc:
            raise WorkflowOperationError(
                "PROVIDER_TIMEOUT", "Cloudflare Workers AI generation timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Cloudflare Workers AI generation unavailable", retryable=True
            ) from exc

        if response.status_code == 429:
            raise WorkflowOperationError(
                "PROVIDER_THROTTLED", "Cloudflare Workers AI generation throttled", retryable=True
            )
        if response.status_code in (401, 403):
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Cloudflare Workers AI authentication failed", retryable=False
            )
        if response.status_code >= 500:
            raise WorkflowOperationError(
                "IMAGE_PROVIDER_UNAVAILABLE", "Cloudflare Workers AI generation unavailable", retryable=True
            )
        if response.status_code >= 400:
            # No distinct moderation-rejection signal is documented for
            # Workers AI: a rejected/invalid prompt is a generic 400, mapped
            # to the same fallback-approved code as any other malformed
            # request (see GenerativeImagePipeline._FALLBACK_APPROVED_CODES).
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Cloudflare Workers AI rejected the request", retryable=False
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Cloudflare Workers AI response was not valid JSON", retryable=False
            ) from exc
        if not isinstance(payload, dict):
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Cloudflare Workers AI response was not a JSON object", retryable=False
            )

        # Cloudflare's API envelope carries a top-level "success" flag on
        # every response; a 2xx status with success=false has been observed
        # from other Workers AI models, so this is checked defensively even
        # though the documented happy path always pairs success=true with 200.
        if payload.get("success") is False:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Cloudflare Workers AI reported failure", retryable=False
            )

        result = payload.get("result")
        image_b64 = result.get("image") if isinstance(result, dict) else None
        if not isinstance(image_b64, str) or not image_b64:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Cloudflare Workers AI response did not include an image", retryable=False
            )

        try:
            data = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT",
                "Cloudflare Workers AI returned invalid base64 image data",
                retryable=False,
            ) from exc

        if not data:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Cloudflare Workers AI returned an empty image", retryable=False
            )
        if len(data) > self._max_download_bytes:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED", "generated image exceeds size limit", retryable=False
            )

        # Decodability of the raw bytes as an actual image is validated
        # downstream by the existing, unchanged ImageProcessor.normalize()
        # pipeline (Pillow decode -> ARTIFACT_VALIDATION_FAILED on failure),
        # shared with every other provider -- not duplicated here.
        return GeneratedImage(data=data, content_type=self._CONTENT_TYPE, seed=None, finish_reason=None)
