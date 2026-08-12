"""Real-FFmpeg integration test, deliberately separate from the mocked unit
suite in test_c8_video.py. Skips automatically when ffmpeg/ffprobe are not
installed -- everything here shells out to a real binary."""

import asyncio
import shutil
import subprocess

import pytest
from PIL import Image

from campaign_worker.video.compositor import build_render_args
from campaign_worker.video.ffmpeg_runner import run_ffmpeg, run_ffprobe

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are not installed in this environment",
)


@pytest.mark.asyncio
async def test_real_ffmpeg_composes_three_images_and_audio_into_a_valid_mp4(tmp_path):
    image_paths = []
    for n, color in enumerate(["red", "green", "blue"], start=1):
        path = tmp_path / f"scene-{n}.jpg"
        Image.new("RGB", (108, 192), color).save(path, format="JPEG")
        image_paths.append(path)

    audio_path = tmp_path / "voiceover.mp3"
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio_path)],
        check=True,
        capture_output=True,
    )

    output_path = tmp_path / "final.mp4"
    args = build_render_args(
        scene_image_paths=image_paths,
        scene_durations=[0.7, 0.7, 0.6],
        audio_path=audio_path,
        output_path=output_path,
        width=108,
        height=192,
        fps=24,
    )

    await run_ffmpeg("ffmpeg", args, timeout_seconds=30)
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    probe = await run_ffprobe("ffprobe", str(output_path), timeout_seconds=10)
    video_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
    audio_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]

    assert len(video_streams) == 1
    assert len(audio_streams) == 1
    assert video_streams[0]["codec_name"] == "h264"
    assert audio_streams[0]["codec_name"] == "aac"
    assert video_streams[0]["width"] == 108
    assert video_streams[0]["height"] == 192
    assert "mp4" in probe["format"]["format_name"]
