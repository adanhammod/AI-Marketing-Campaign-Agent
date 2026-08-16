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


async def _extract_frame(video_path, timestamp_seconds, output_path):
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-v", "error", "-ss", str(timestamp_seconds), "-i", str(video_path), "-frames:v", "1", str(output_path)],
        check=True,
        capture_output=True,
    )


def _average_color(image_path):
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        pixels = list(image.getdata())
    channels = zip(*pixels, strict=True)
    return tuple(sum(channel) / len(pixels) for channel in channels)


def _closest_color(sample, palette):
    def distance(color):
        return sum((a - b) ** 2 for a, b in zip(sample, color, strict=True))

    return min(palette, key=distance)


@pytest.mark.asyncio
async def test_real_ffmpeg_shows_each_scenes_own_image_at_its_own_timestamp(tmp_path):
    # Regression test for the "video shows only one image throughout"
    # bug: zoompan's `d` parameter did not itself bound a scene chain's
    # output frame count, so concat never advanced past the first scene.
    # This renders three genuinely distinct (solid-color) scenes at the
    # real target resolution/fps and proves, from the actual decoded
    # output, that each scene's midpoint frame shows that scene's color.
    palette = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}
    image_paths = []
    for n, name in enumerate(["red", "green", "blue"], start=1):
        path = tmp_path / f"scene-{n}.jpg"
        Image.new("RGB", (1080, 1920), palette[name]).save(path, format="JPEG", quality=95)
        image_paths.append(path)

    scene_duration = 1.2
    total_duration = scene_duration * 3
    audio_path = tmp_path / "voiceover.mp3"
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={total_duration}", str(audio_path)],
        check=True,
        capture_output=True,
    )

    output_path = tmp_path / "final.mp4"
    args = build_render_args(
        scene_image_paths=image_paths,
        scene_durations=[scene_duration, scene_duration, scene_duration],
        audio_path=audio_path,
        output_path=output_path,
        width=1080,
        height=1920,
        fps=30,
    )
    await run_ffmpeg("ffmpeg", args, timeout_seconds=60)

    probe = await run_ffprobe("ffprobe", str(output_path), timeout_seconds=10)
    video_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
    audio_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
    assert video_streams[0]["width"] == 1080
    assert video_streams[0]["height"] == 1920
    assert video_streams[0]["codec_name"] == "h264"
    assert video_streams[0]["avg_frame_rate"] == "30/1"
    assert len(audio_streams) == 1

    midpoints = [scene_duration * (index + 0.5) for index in range(3)]
    frame_paths = [tmp_path / f"frame-{index}.jpg" for index in range(3)]
    for midpoint, frame_path in zip(midpoints, frame_paths, strict=True):
        await _extract_frame(output_path, midpoint, frame_path)

    frame_colors = [_average_color(path) for path in frame_paths]
    expected = [palette["red"], palette["green"], palette["blue"]]
    for observed, expected_color in zip(frame_colors, expected, strict=True):
        assert _closest_color(observed, list(palette.values())) == expected_color

    frame_bytes = [path.read_bytes() for path in frame_paths]
    assert len({frame_bytes[0], frame_bytes[1], frame_bytes[2]}) == 3
