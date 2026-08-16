import hashlib
from pathlib import Path

import pytest
from campaign_contracts.campaign import Storyboard, StoryboardScene

from campaign_worker.video.hyperframes_renderer import HyperFramesVideoRenderer
from campaign_worker.video.models import LocalRenderRequest


def _storyboard() -> Storyboard:
    return Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration=f"Narration for scene {n}.",
                visual_prompt=f"cold brew scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )


def _request(tmp_path: Path, **overrides) -> LocalRenderRequest:
    scene_image_paths = {n: tmp_path / f"src-scene-{n}.jpg" for n in (1, 2, 3)}
    for path in scene_image_paths.values():
        path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    audio_path = tmp_path / "src-voiceover.mp3"
    audio_path.write_bytes(b"fake-mp3-bytes")
    defaults = dict(
        scene_image_paths=scene_image_paths,
        scene_durations={1: 5.0, 2: 5.0, 3: 5.0},
        audio_path=audio_path,
        storyboard=_storyboard(),
        headline="Luna Cold Brew",
        key_message="Smooth energy for your day.",
        cta="Discover the collection.",
        output_path=tmp_path / "out" / "final.mp4",
        width=1080,
        height=1920,
        fps=30,
    )
    defaults.update(overrides)
    (tmp_path / "out").mkdir(exist_ok=True)
    return LocalRenderRequest(**defaults)


class _RecordingCliRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        # Captured inside the callback -- the renderer cleans up its
        # TemporaryDirectory as soon as render() returns, so the project dir
        # no longer exists by the time a test could inspect it afterward.
        self.staged_filenames: set[str] = set()
        self.composition_html: str = ""

    async def __call__(self, npx_path, args, *, timeout_seconds):
        self.calls.append(args)
        project_dir = Path(args[2])
        self.staged_filenames = {p.name for p in project_dir.iterdir()}
        self.composition_html = (project_dir / "index.html").read_text()
        output_path = Path(args[args.index("-o") + 1])
        output_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 500)


@pytest.mark.asyncio
async def test_hyperframes_renderer_stages_all_three_scene_images_and_audio_into_the_project_dir(tmp_path):
    request = _request(tmp_path)
    runner = _RecordingCliRunner()
    renderer = HyperFramesVideoRenderer(npx_path="npx", hyperframes_cli_runner=runner, render_timeout_seconds=30)

    await renderer.render(request)

    assert {"index.html", "scene-1.jpg", "scene-2.jpg", "scene-3.jpg", "voiceover.mp3"} <= runner.staged_filenames


@pytest.mark.asyncio
async def test_hyperframes_renderer_writes_a_composition_referencing_the_staged_filenames(tmp_path):
    request = _request(tmp_path)
    runner = _RecordingCliRunner()
    renderer = HyperFramesVideoRenderer(npx_path="npx", hyperframes_cli_runner=runner, render_timeout_seconds=30)

    await renderer.render(request)

    assert "scene-1.jpg" in runner.composition_html
    assert "voiceover.mp3" in runner.composition_html
    assert "Luna Cold Brew" in runner.composition_html


@pytest.mark.asyncio
async def test_hyperframes_renderer_invokes_cli_with_output_path_and_fps(tmp_path):
    request = _request(tmp_path)
    runner = _RecordingCliRunner()
    renderer = HyperFramesVideoRenderer(npx_path="npx", hyperframes_cli_runner=runner, render_timeout_seconds=30)

    await renderer.render(request)

    args = runner.calls[0]
    assert args[0:2] == ["hyperframes", "render"]
    assert args[args.index("-o") + 1] == str(request.output_path)
    assert args[args.index("-f") + 1] == "30"


@pytest.mark.asyncio
async def test_hyperframes_renderer_returns_rendered_video_with_checksum_and_target_dimensions(tmp_path):
    request = _request(tmp_path)
    runner = _RecordingCliRunner()
    renderer = HyperFramesVideoRenderer(npx_path="npx", hyperframes_cli_runner=runner, render_timeout_seconds=30)

    rendered = await renderer.render(request)

    expected_bytes = request.output_path.read_bytes()
    assert rendered.checksum_sha256 == hashlib.sha256(expected_bytes).hexdigest()
    assert rendered.width == 1080
    assert rendered.height == 1920
    assert rendered.fps == 30
    assert rendered.duration_seconds == 15.0
    assert rendered.video_codec == "h264"
    assert rendered.audio_codec == "aac"


def test_hyperframes_renderer_name_is_hyperframes():
    renderer = HyperFramesVideoRenderer()
    assert renderer.name == "hyperframes"
