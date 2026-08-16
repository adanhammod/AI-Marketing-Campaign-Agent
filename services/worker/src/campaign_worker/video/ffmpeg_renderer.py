import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from .compositor import build_render_args
from .ffmpeg_runner import run_ffmpeg
from .models import LocalRenderRequest, RenderedVideo


class FfmpegVideoRenderer:
    """Pure rendering: local scene images + audio -> RenderedVideo, via FFmpeg.

    Thin wrapper around the existing compositor.build_render_args() and
    ffmpeg_runner.run_ffmpeg() -- unmodified, so the FFmpeg render path's
    behavior (and its existing test coverage) is unchanged by extracting it
    behind the VideoRenderer protocol.
    """

    name = "ffmpeg"

    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        render_timeout_seconds: float = 60,
        ffmpeg_runner: Callable[..., Awaitable[None]] = run_ffmpeg,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._render_timeout_seconds = render_timeout_seconds
        self._ffmpeg_runner = ffmpeg_runner

    async def render(self, request: LocalRenderRequest) -> RenderedVideo:
        scene_numbers = sorted(request.scene_image_paths)
        scene_image_paths: list[Path] = [request.scene_image_paths[n] for n in scene_numbers]
        scene_durations: list[float] = [request.scene_durations[n] for n in scene_numbers]

        args = build_render_args(
            scene_image_paths=scene_image_paths,
            scene_durations=scene_durations,
            audio_path=request.audio_path,
            output_path=request.output_path,
            width=request.width,
            height=request.height,
            fps=request.fps,
        )
        await self._ffmpeg_runner(self._ffmpeg_path, args, timeout_seconds=self._render_timeout_seconds)

        data = request.output_path.read_bytes()
        return RenderedVideo(
            data=data,
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            width=request.width,
            height=request.height,
            duration_seconds=sum(scene_durations),
            video_codec="h264",
            audio_codec="aac",
            fps=request.fps,
        )
