"""Opt-in local video-render smoke test.

There is no HyperFrames integration in this codebase (only researched/planned,
never implemented) -- this verifies the actual, currently-implemented video
renderer instead: image -> ffmpeg compositor -> rendered MP4. Reuses
compositor.build_render_args() and ffmpeg_runner.run_ffmpeg()/run_ffprobe()
completely unmodified. Entirely local (ffmpeg subprocess), not billable, not
part of the normal test suite: double-gated by the `live_smoke` marker
(excluded from the default `-m 'not live_smoke'` addopts) AND a `pytest.skip`
unless RUN_VIDEO_RENDER_LIVE_SMOKE is explicitly set.

    RUN_VIDEO_RENDER_LIVE_SMOKE=1 uv run pytest -m live_smoke tests/smoke/test_video_render_smoke.py
"""

import os
from pathlib import Path

import pytest

from campaign_worker.video.compositor import build_render_args
from campaign_worker.video.ffmpeg_runner import run_ffmpeg, run_ffprobe

pytestmark = pytest.mark.live_smoke

_SOURCE_IMAGE = Path("/tmp/stability-smoke-output.jpg")
_SILENT_AUDIO = Path("/tmp/video-render-smoke-silence.wav")
_OUTPUT_VIDEO = Path("/tmp/video-render-smoke-output.mp4")
_DURATION_SECONDS = 6.0


@pytest.mark.asyncio
async def test_video_render_produces_one_local_video():
    if not os.getenv("RUN_VIDEO_RENDER_LIVE_SMOKE"):
        pytest.skip("set RUN_VIDEO_RENDER_LIVE_SMOKE=1 to run this opt-in local render smoke test")

    assert _SOURCE_IMAGE.exists(), f"expected source image at {_SOURCE_IMAGE}"

    # Synthesize a short silent placeholder track locally -- not a voiceover,
    # not Polly, not any external call -- purely to satisfy
    # build_render_args()'s required audio_path parameter unmodified.
    await run_ffmpeg(
        "ffmpeg",
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{_DURATION_SECONDS:.3f}",
            str(_SILENT_AUDIO),
        ],
        timeout_seconds=30,
    )

    args = build_render_args(
        scene_image_paths=[_SOURCE_IMAGE],
        scene_durations=[_DURATION_SECONDS],
        audio_path=_SILENT_AUDIO,
        output_path=_OUTPUT_VIDEO,
        width=1080,
        height=1920,
        fps=30,
    )

    # The one real render call this smoke test makes.
    await run_ffmpeg("ffmpeg", args, timeout_seconds=60)

    probe = await run_ffprobe("ffprobe", str(_OUTPUT_VIDEO), timeout_seconds=30)
    streams = probe.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    assert len(video_streams) == 1
    assert len(audio_streams) == 1
    assert video_streams[0].get("codec_name") == "h264"
    assert video_streams[0].get("width") == 1080
    assert video_streams[0].get("height") == 1920
    assert audio_streams[0].get("codec_name") == "aac"
    assert "mp4" in probe.get("format", {}).get("format_name", "")

    print(f"saved smoke-test video to {_OUTPUT_VIDEO}")
