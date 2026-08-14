import email

import httpx
import pytest

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.providers.stability_client import StabilityImageClient


def _multipart_fields(request: httpx.Request) -> dict[str, str]:
    content_type = request.headers["content-type"]
    header_bytes = f"Content-Type: {content_type}\r\n\r\n".encode()
    message = email.message_from_bytes(header_bytes + request.content)
    fields: dict[str, str] = {}
    for part in message.walk():
        name = part.get_param("name", header="Content-Disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True)
        fields[str(name)] = payload.decode() if payload else ""
    return fields


def _jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 10


def _success_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=_jpeg_bytes(),
        headers={"content-type": "image/jpeg", "finish-reason": "SUCCESS", "seed": "42424242"},
    )


@pytest.mark.asyncio
async def test_stability_request_includes_configured_model_field():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success_handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        await stability.generate("a coffee ad", "no text")

    fields = _multipart_fields(seen[0])
    assert fields["model"] == "sd3.5-large"
    assert fields["prompt"] == "a coffee ad"
    assert fields["negative_prompt"] == "no text"
    assert seen[0].headers["authorization"] == "Bearer secret-key"


@pytest.mark.asyncio
async def test_stability_request_includes_configured_aspect_ratio_and_output_format():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success_handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stability = StabilityImageClient(
            "secret-key", client, model="sd3.5-large", aspect_ratio="9:16", output_format="jpeg"
        )
        await stability.generate("a coffee ad", "no text")

    fields = _multipart_fields(seen[0])
    assert fields["aspect_ratio"] == "9:16"
    assert fields["output_format"] == "jpeg"


@pytest.mark.asyncio
async def test_stability_successful_generation_returns_image_bytes_and_seed():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_success_handler)) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        result = await stability.generate("a coffee ad", "no text")

    assert result.data == _jpeg_bytes()
    assert result.content_type == "image/jpeg"
    assert result.seed == 42424242
    assert result.finish_reason == "SUCCESS"


@pytest.mark.asyncio
async def test_stability_timeout_maps_to_provider_timeout():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "PROVIDER_TIMEOUT"
        assert error.value.retryable is True


@pytest.mark.asyncio
async def test_stability_5xx_and_connection_errors_map_to_image_provider_unavailable():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "IMAGE_PROVIDER_UNAVAILABLE"
        assert error.value.retryable is True

    def connection_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(connection_error)) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "IMAGE_PROVIDER_UNAVAILABLE"
        assert error.value.retryable is True


@pytest.mark.asyncio
async def test_stability_429_maps_to_provider_throttled_and_402_maps_to_credits_code():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "PROVIDER_THROTTLED"
        assert error.value.retryable is True

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(402))) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "PROVIDER_THROTTLED"
        assert error.value.retryable is False


@pytest.mark.asyncio
async def test_stability_malformed_response_and_oversized_body_map_to_typed_errors():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})
        )
    ) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"

    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_jpeg_bytes(), headers={"content-type": "image/jpeg", "finish-reason": "SUCCESS"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
        stability = StabilityImageClient(
            "secret-key", client, model="sd3.5-large", aspect_ratio="9:16", max_download_bytes=5
        )
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_stability_moderation_rejection_maps_to_provider_policy_rejection():
    def filtered(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_jpeg_bytes(), headers={"content-type": "image/jpeg", "finish-reason": "CONTENT_FILTERED"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(filtered)) as client:
        stability = StabilityImageClient("secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
        with pytest.raises(WorkflowOperationError) as error:
            await stability.generate("a coffee ad", "no text")
        assert error.value.code == "PROVIDER_POLICY_REJECTION"
        assert error.value.retryable is False


@pytest.mark.asyncio
async def test_stability_client_never_logs_or_raises_the_api_key_in_any_error_message():
    for status in (401, 403, 429, 402, 500):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, response_status=status: httpx.Response(response_status))
        ) as client:
            stability = StabilityImageClient("super-secret-key", client, model="sd3.5-large", aspect_ratio="9:16")
            with pytest.raises(WorkflowOperationError) as error:
                await stability.generate("a coffee ad", "no text")
            assert "super-secret-key" not in str(error.value)
            assert "super-secret-key" not in repr(error.value)
