import hashlib
from pathlib import Path

import pytest
from campaign_contracts.campaign import Storyboard, StoryboardScene

from campaign_worker.video.ffmpeg_renderer import FfmpegVideoRenderer
from campaign_worker.video.models import LocalRenderRequest


def _storyboard() -> Storyboard:
    return Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration="Fresh coffee delivered weekly.",
                visual_prompt=f"artisan cold brew scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )


def _request(tmp_path: Path, **overrides) -> LocalRenderRequest:
    scene_image_paths = {n: tmp_path / f"scene-{n}.jpg" for n in (1, 2, 3)}
    for path in scene_image_paths.values():
        path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    defaults = dict(
        scene_image_paths=scene_image_paths,
        scene_durations={1: 5.0, 2: 5.0, 3: 5.0},
        audio_path=tmp_path / "voiceover.mp3",
        storyboard=_storyboard(),
        headline="Luna Cold Brew",
        key_message="Smooth energy for your day.",
        cta="Discover the collection.",
        output_path=tmp_path / "final.mp4",
        width=1080,
        height=1920,
        fps=30,
    )
    defaults.update(overrides)
    (tmp_path / "voiceover.mp3").write_bytes(b"fake-mp3-bytes")
    return LocalRenderRequest(**defaults)


class _RecordingFfmpegRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, ffmpeg_path, args, *, timeout_seconds):
        self.calls.append(args)
        Path(args[-1]).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 500)


@pytest.mark.asyncio
async def test_ffmpeg_renderer_invokes_ffmpeg_with_scenes_in_scene_number_order(tmp_path):
    request = _request(tmp_path)
    runner = _RecordingFfmpegRunner()
    renderer = FfmpegVideoRenderer(ffmpeg_path="ffmpeg", ffmpeg_runner=runner, render_timeout_seconds=30)

    await renderer.render(request)

    assert len(runner.calls) == 1
    args = runner.calls[0]
    input_paths = [args[i + 1] for i, token in enumerate(args) if token == "-i"]
    assert [Path(p).name for p in input_paths] == ["scene-1.jpg", "scene-2.jpg", "scene-3.jpg", "voiceover.mp3"]


@pytest.mark.asyncio
async def test_ffmpeg_renderer_returns_rendered_video_with_checksum_and_target_dimensions(tmp_path):
    request = _request(tmp_path)
    runner = _RecordingFfmpegRunner()
    renderer = FfmpegVideoRenderer(ffmpeg_path="ffmpeg", ffmpeg_runner=runner, render_timeout_seconds=30)

    rendered = await renderer.render(request)

    expected_bytes = request.output_path.read_bytes()
    assert rendered.checksum_sha256 == hashlib.sha256(expected_bytes).hexdigest()
    assert rendered.width == 1080
    assert rendered.height == 1920
    assert rendered.fps == 30
    assert rendered.duration_seconds == 15.0
    assert rendered.video_codec == "h264"
    assert rendered.audio_codec == "aac"
