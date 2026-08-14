"""Opt-in smoke test against the REAL Stability AI API.

Never runs as part of the normal test suite: it is double-gated by the
`live_smoke` pytest marker (excluded from the default `-m 'not live_smoke'`
addopts in pyproject.toml) AND a `pytest.skip` unless RUN_STABILITY_LIVE_SMOKE
is explicitly set. Running this makes exactly one real, billable Stability
API call. Do not run it without first showing the user the exact model,
prompt, expected credit cost, and output path, and getting explicit approval.

    RUN_STABILITY_LIVE_SMOKE=1 uv run pytest -m live_smoke tests/smoke/test_stability_live_smoke.py
"""

import os

import httpx
import pytest

from campaign_worker.providers.stability_client import StabilityImageClient

pytestmark = pytest.mark.live_smoke


@pytest.mark.asyncio
async def test_stability_live_generation_produces_one_real_image():
    if not os.getenv("RUN_STABILITY_LIVE_SMOKE"):
        pytest.skip("set RUN_STABILITY_LIVE_SMOKE=1 to run this billable, opt-in smoke test")

    api_key = os.getenv("STABILITY_API_KEY")
    assert api_key, "STABILITY_API_KEY must be set in the environment to run this test"

    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        stability = StabilityImageClient(api_key, client, model="sd3.5-large", aspect_ratio="9:16")
        result = await stability.generate(
            "Professional advertising photography test image, vertical 9:16 composition, "
            "cinematic lighting, photorealistic.",
            "no text, no typography, no captions, no watermark, no logos",
        )

    assert result.data
    assert result.content_type.startswith("image/")
