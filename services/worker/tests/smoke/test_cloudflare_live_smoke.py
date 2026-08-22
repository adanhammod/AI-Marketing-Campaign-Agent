"""Opt-in smoke test against the REAL Cloudflare Workers AI API.

Never runs as part of the normal test suite: it is double-gated by the
`live_smoke` pytest marker (excluded from the default `-m 'not live_smoke'`
addopts in pyproject.toml) AND a `pytest.skip` unless RUN_CLOUDFLARE_LIVE_SMOKE
is explicitly set. Running this makes exactly one real, billable Workers AI
call (billed per-neuron). Do not run it without first showing the user the
exact model, prompt, expected cost, and output path, and getting explicit
approval.

    RUN_CLOUDFLARE_LIVE_SMOKE=1 uv run pytest -m live_smoke tests/smoke/test_cloudflare_live_smoke.py
"""

import os
from pathlib import Path

import httpx
import pytest

from campaign_worker.providers.cloudflare_flux_client import CloudflareFluxClient

pytestmark = pytest.mark.live_smoke


@pytest.mark.asyncio
async def test_cloudflare_live_generation_produces_one_real_image():
    if not os.getenv("RUN_CLOUDFLARE_LIVE_SMOKE"):
        pytest.skip("set RUN_CLOUDFLARE_LIVE_SMOKE=1 to run this billable, opt-in smoke test")

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    assert account_id, "CLOUDFLARE_ACCOUNT_ID must be set in the environment to run this test"
    assert api_token, "CLOUDFLARE_API_TOKEN must be set in the environment to run this test"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        flux = CloudflareFluxClient(
            account_id, api_token, client, model="@cf/black-forest-labs/flux-1-schnell"
        )
        result = await flux.generate(
            "Professional advertising photography test image, vertical 9:16 composition, "
            "cinematic lighting, photorealistic.",
            "no text, no typography, no captions, no watermark, no logos",
        )

    assert result.data
    assert result.content_type.startswith("image/")

    output_path = Path("/tmp/cloudflare-flux-smoke-output.jpg")
    output_path.write_bytes(result.data)
    print(f"saved smoke-test image to {output_path}")
