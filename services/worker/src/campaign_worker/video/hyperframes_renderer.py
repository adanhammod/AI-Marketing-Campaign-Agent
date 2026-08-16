import hashlib
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from .hyperframes_composition import build_composition_html
from .hyperframes_runner import run_hyperframes_cli
from .models import LocalRenderRequest, RenderedVideo


class HyperFramesVideoRenderer:
    """Pure rendering: local scene images + audio -> RenderedVideo, via the
    open-source `hyperframes` CLI (heygen-com/hyperframes), run entirely
    locally through `npx` -- headless Chrome + FFmpeg under the hood, no
    HeyGen account or OAuth involved.
    """

    name = "hyperframes"

    def __init__(
        self,
        *,
        npx_path: str = "npx",
        render_timeout_seconds: float = 180,
        hyperframes_cli_runner: Callable[..., Awaitable[None]] = run_hyperframes_cli,
        workdir_prefix: str = "c8-hyperframes-",
    ) -> None:
        self._npx_path = npx_path
        self._render_timeout_seconds = render_timeout_seconds
        self._hyperframes_cli_runner = hyperframes_cli_runner
        self._workdir_prefix = workdir_prefix

    async def render(self, request: LocalRenderRequest) -> RenderedVideo:
        with tempfile.TemporaryDirectory(prefix=self._workdir_prefix) as tmp:
            project_dir = Path(tmp)

            image_filenames: dict[int, str] = {}
            for scene_number, source_path in request.scene_image_paths.items():
                filename = f"scene-{scene_number}.jpg"
                shutil.copyfile(source_path, project_dir / filename)
                image_filenames[scene_number] = filename

            audio_filename = "voiceover.mp3"
            shutil.copyfile(request.audio_path, project_dir / audio_filename)

            html = build_composition_html(request, image_filenames=image_filenames, audio_filename=audio_filename)
            (project_dir / "index.html").write_text(html)

            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            args = [
                "hyperframes",
                "render",
                str(project_dir),
                "-o",
                str(request.output_path),
                "-f",
                str(request.fps),
            ]
            await self._hyperframes_cli_runner(self._npx_path, args, timeout_seconds=self._render_timeout_seconds)

            data = request.output_path.read_bytes()
            return RenderedVideo(
                data=data,
                checksum_sha256=hashlib.sha256(data).hexdigest(),
                width=request.width,
                height=request.height,
                duration_seconds=sum(request.scene_durations.values()),
                video_codec="h264",
                audio_codec="aac",
                fps=request.fps,
            )
