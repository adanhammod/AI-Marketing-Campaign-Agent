import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.video.ffmpeg_runner import run_ffmpeg, run_ffprobe

# ffmpeg's atempo filter accepts [0.5, 100.0] in a single stage; this bound is
# far tighter -- a deliberately conservative "barely audible" ceiling for
# correcting a narration duration that estimation error let through. Anything
# needing more correction than this is a genuinely pathological input (e.g.
# narration many seconds too long/short for its storyboard) and should fail
# loudly rather than being stretched into something that no longer sounds
# like the intended read.
MIN_TEMPO_FACTOR = 0.92
MAX_TEMPO_FACTOR = 1.08

# Safety margin so the post-adjustment re-probe reliably lands inside
# [min_duration_seconds, max_duration_seconds] despite float/encoding
# rounding in ffmpeg's own tempo-shifted output duration.
_BOUNDARY_MARGIN_SECONDS = 0.1

_OUTPUT_BITRATE = "48k"


@dataclass(frozen=True, slots=True)
class DurationCheckResult:
    data: bytes
    measured_duration_seconds: float
    tempo_factor: float | None  # None if no adjustment was needed/applied


async def _probe_duration(
    ffprobe_runner: Callable[..., Awaitable[dict[str, Any]]], ffprobe_path: str, path: Path, timeout_seconds: float
) -> float:
    probe = await ffprobe_runner(ffprobe_path, str(path), timeout_seconds=timeout_seconds)
    try:
        return float(probe["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowOperationError(
            "INVALID_PROVIDER_OUTPUT", "ffprobe returned no readable audio duration", retryable=True
        ) from exc


async def ensure_duration_in_range(
    mp3_bytes: bytes,
    *,
    min_duration_seconds: float,
    max_duration_seconds: float,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout_seconds: float = 30,
    ffmpeg_runner: Callable[..., Awaitable[None]] = run_ffmpeg,
    ffprobe_runner: Callable[..., Awaitable[dict[str, Any]]] = run_ffprobe,
) -> DurationCheckResult:
    """Measures mp3_bytes' real duration and, only if it falls outside
    [min_duration_seconds, max_duration_seconds], applies a single bounded
    ffmpeg atempo adjustment (never audio trimming, never touching content)
    to bring it back in range, re-probing to confirm. Raises
    ARTIFACT_VALIDATION_FAILED (non-retryable) if the required correction
    exceeds the safe [MIN_TEMPO_FACTOR, MAX_TEMPO_FACTOR] bound, or if the
    adjusted audio still isn't in range after correction.
    """
    with tempfile.TemporaryDirectory(prefix="c-duration-") as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "input.mp3"
        input_path.write_bytes(mp3_bytes)

        duration = await _probe_duration(ffprobe_runner, ffprobe_path, input_path, timeout_seconds)
        if min_duration_seconds <= duration <= max_duration_seconds:
            return DurationCheckResult(data=mp3_bytes, measured_duration_seconds=duration, tempo_factor=None)

        if duration > max_duration_seconds:
            target = max_duration_seconds - _BOUNDARY_MARGIN_SECONDS
        else:
            target = min_duration_seconds + _BOUNDARY_MARGIN_SECONDS
        factor = duration / target

        if not (MIN_TEMPO_FACTOR <= factor <= MAX_TEMPO_FACTOR):
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED",
                "narration duration incompatible with storyboard timing",
                retryable=False,
            )

        output_path = tmp_path / "tempo.mp3"
        args = [
            "-y",
            "-i",
            str(input_path),
            "-af",
            f"atempo={factor:.6f}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            _OUTPUT_BITRATE,
            str(output_path),
        ]
        await ffmpeg_runner(
            ffmpeg_path, args, timeout_seconds=timeout_seconds, unavailable_code="VOICE_PROVIDER_UNAVAILABLE"
        )
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "tempo normalization produced no output", retryable=True
            )

        adjusted_bytes = output_path.read_bytes()
        adjusted_duration = await _probe_duration(ffprobe_runner, ffprobe_path, output_path, timeout_seconds)
        if not (min_duration_seconds <= adjusted_duration <= max_duration_seconds):
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED",
                "narration duration incompatible with storyboard timing",
                retryable=False,
            )
        return DurationCheckResult(data=adjusted_bytes, measured_duration_seconds=adjusted_duration, tempo_factor=factor)
