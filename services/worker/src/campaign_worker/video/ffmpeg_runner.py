import asyncio
import json
from typing import Any

from campaign_worker.errors import WorkflowOperationError

_STDERR_TAIL_CHARS = 500


def _sanitize_stderr(stderr: bytes) -> str:
    text = stderr.decode(errors="replace").strip()
    return text[-_STDERR_TAIL_CHARS:]


async def run_subprocess(
    binary: str,
    args: list[str],
    *,
    timeout_seconds: float,
    unavailable_code: str = "VIDEO_PROVIDER_UNAVAILABLE",
) -> tuple[bytes, bytes, int]:
    try:
        process = await asyncio.create_subprocess_exec(
            binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:
        raise WorkflowOperationError(unavailable_code, f"{binary} binary not found", retryable=False) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise WorkflowOperationError("PROVIDER_TIMEOUT", f"{binary} timed out", retryable=True) from exc
    return stdout, stderr, process.returncode  # type: ignore[return-value]


async def run_ffmpeg(
    ffmpeg_path: str,
    args: list[str],
    *,
    timeout_seconds: float,
    unavailable_code: str = "VIDEO_PROVIDER_UNAVAILABLE",
) -> None:
    _, stderr, returncode = await run_subprocess(
        ffmpeg_path, args, timeout_seconds=timeout_seconds, unavailable_code=unavailable_code
    )
    if returncode != 0:
        raise WorkflowOperationError(
            unavailable_code,
            f"ffmpeg exited with {returncode}: {_sanitize_stderr(stderr)}",
            retryable=True,
        )


async def run_ffmpeg_capturing_stderr(
    ffmpeg_path: str,
    args: list[str],
    *,
    timeout_seconds: float,
    unavailable_code: str = "VIDEO_PROVIDER_UNAVAILABLE",
) -> str:
    # Like run_ffmpeg, but returns raw stderr text on success instead of
    # discarding it. Used by audio/normalizer.py to read the loudnorm
    # filter's measured-loudness JSON stats block, which ffmpeg only ever
    # prints to stderr -- never to stdout or the output file.
    _, stderr, returncode = await run_subprocess(
        ffmpeg_path, args, timeout_seconds=timeout_seconds, unavailable_code=unavailable_code
    )
    if returncode != 0:
        raise WorkflowOperationError(
            unavailable_code,
            f"ffmpeg exited with {returncode}: {_sanitize_stderr(stderr)}",
            retryable=True,
        )
    return stderr.decode(errors="replace")


async def run_ffprobe(
    ffprobe_path: str,
    file_path: str,
    *,
    timeout_seconds: float,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    args = [*(extra_args or []), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", file_path]
    stdout, _, returncode = await run_subprocess(ffprobe_path, args, timeout_seconds=timeout_seconds)
    if returncode != 0:
        raise WorkflowOperationError("INVALID_PROVIDER_OUTPUT", "ffprobe rejected the file", retryable=True)
    try:
        result: dict[str, Any] = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowOperationError(
            "INVALID_PROVIDER_OUTPUT", "ffprobe returned malformed output", retryable=True
        ) from exc
    return result
