import base64
import json

import httpx
import pytest

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.providers.cloudflare_flux_client import CloudflareFluxClient


def _jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 10


def _envelope(image_b64: str | None = "not-checked", *, success: bool = True) -> dict:
    body: dict = {"success": success, "errors": [], "messages": []}
    if image_b64 is not None:
        body["result"] = {"image": image_b64}
    return body


def _success_handler(request: httpx.Request) -> httpx.Response:
    encoded = base64.b64encode(_jpeg_bytes()).decode()
    return httpx.Response(200, json=_envelope(encoded))


@pytest.mark.asyncio
async def test_cloudflare_request_targets_the_configured_model_and_account_id():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success_handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        flux = CloudflareFluxClient(
            "acct-123", "secret-token", client, model="@cf/black-forest-labs/flux-1-schnell"
        )
        await flux.generate("a coffee ad", "no text")

    assert str(seen[0].url) == (
        "https://api.cloudflare.com/client/v4/accounts/acct-123/ai/run/@cf/black-forest-labs/flux-1-schnell"
    )
    assert seen[0].headers["authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_cloudflare_request_body_sends_prompt_and_steps_as_json_and_ignores_negative_prompt():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success_handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        flux = CloudflareFluxClient(
            "acct-123", "secret-token", client, model="@cf/black-forest-labs/flux-1-schnell", steps=6
        )
        await flux.generate("a coffee ad", "no text, no watermark")

    body = json.loads(seen[0].content)
    assert body == {"prompt": "a coffee ad", "steps": 6}


@pytest.mark.asyncio
async def test_cloudflare_successful_generation_decodes_base64_json_envelope_as_jpeg():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_success_handler)) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        result = await flux.generate("a coffee ad", "no text")

    assert result.data == _jpeg_bytes()
    assert result.content_type == "image/jpeg"
    assert result.seed is None
    assert result.finish_reason is None


@pytest.mark.asyncio
async def test_cloudflare_timeout_maps_to_provider_timeout():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "PROVIDER_TIMEOUT"
        assert error.value.retryable is True


@pytest.mark.asyncio
async def test_cloudflare_5xx_and_connection_errors_map_to_image_provider_unavailable():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "IMAGE_PROVIDER_UNAVAILABLE"
        assert error.value.retryable is True

    def connection_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(connection_error)) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "IMAGE_PROVIDER_UNAVAILABLE"
        assert error.value.retryable is True


@pytest.mark.asyncio
async def test_cloudflare_429_maps_to_provider_throttled():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "PROVIDER_THROTTLED"
        assert error.value.retryable is True


@pytest.mark.asyncio
async def test_cloudflare_401_and_403_map_to_image_provider_unavailable_non_retryable():
    for status in (401, 403):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, s=status: httpx.Response(s))
        ) as client:
            flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
            with pytest.raises(WorkflowOperationError) as error:
                await flux.generate("a coffee ad", "no text")
            assert error.value.code == "IMAGE_PROVIDER_UNAVAILABLE"
            assert error.value.retryable is False


@pytest.mark.asyncio
async def test_cloudflare_generic_400_maps_to_invalid_provider_output_which_is_fallback_approved():
    # Workers AI has no distinct moderation-rejection signal like Stability's
    # finish-reason header -- a rejected/invalid prompt is a generic 400,
    # which must map to a fallback-approved code (confirmed behavior: falls
    # back to Pexels rather than hard-failing the campaign).
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(400))) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"
        assert error.value.retryable is False


@pytest.mark.asyncio
async def test_cloudflare_malformed_json_maps_to_invalid_provider_output():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"not json at all", headers={"content-type": "application/json"})
        )
    ) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_cloudflare_json_that_is_not_an_object_maps_to_invalid_provider_output():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[1, 2, 3]))) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_cloudflare_success_false_maps_to_invalid_provider_output():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_envelope(None, success=False)))
    ) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"
        assert error.value.retryable is False


@pytest.mark.asyncio
async def test_cloudflare_missing_image_field_maps_to_invalid_provider_output():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"success": True, "result": {}}))
    ) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_cloudflare_missing_result_field_maps_to_invalid_provider_output():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"success": True}))
    ) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_cloudflare_empty_image_string_maps_to_invalid_provider_output():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_envelope("")))
    ) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_cloudflare_invalid_base64_maps_to_invalid_provider_output():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_envelope("not-valid-base64!!!")))
    ) as client:
        flux = CloudflareFluxClient("acct-123", "secret-token", client, model="flux-1-schnell")
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_cloudflare_oversized_decoded_image_maps_to_artifact_validation_failed():
    encoded = base64.b64encode(_jpeg_bytes()).decode()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_envelope(encoded)))
    ) as client:
        flux = CloudflareFluxClient(
            "acct-123", "secret-token", client, model="flux-1-schnell", max_download_bytes=5
        )
        with pytest.raises(WorkflowOperationError) as error:
            await flux.generate("a coffee ad", "no text")
        assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_cloudflare_client_never_logs_or_raises_the_api_token_in_any_error_message():
    for status in (401, 403, 429, 400, 500):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, response_status=status: httpx.Response(response_status))
        ) as client:
            flux = CloudflareFluxClient("acct-123", "super-secret-token", client, model="flux-1-schnell")
            with pytest.raises(WorkflowOperationError) as error:
                await flux.generate("a coffee ad", "no text")
            assert "super-secret-token" not in str(error.value)
            assert "super-secret-token" not in repr(error.value)


def test_cloudflare_constructor_rejects_missing_account_id_token_or_model():
    with pytest.raises(ValueError, match="account ID"):
        CloudflareFluxClient("", "token", httpx.AsyncClient(), model="flux-1-schnell")
    with pytest.raises(ValueError, match="API token"):
        CloudflareFluxClient("acct", "", httpx.AsyncClient(), model="flux-1-schnell")
    with pytest.raises(ValueError, match="model"):
        CloudflareFluxClient("acct", "token", httpx.AsyncClient(), model="")
