from campaign_worker.errors import WorkflowOperationError

from .ffmpeg_runner import run_subprocess

_STDERR_TAIL_CHARS = 500


def _sanitize_stderr(stderr: bytes) -> str:
    text = stderr.decode(errors="replace").strip()
    return text[-_STDERR_TAIL_CHARS:]


async def run_hyperframes_cli(
    npx_path: str,
    args: list[str],
    *,
    timeout_seconds: float,
) -> None:
    _, stderr, returncode = await run_subprocess(npx_path, args, timeout_seconds=timeout_seconds)
    if returncode != 0:
        raise WorkflowOperationError(
            "VIDEO_PROVIDER_UNAVAILABLE",
            f"hyperframes render exited with {returncode}: {_sanitize_stderr(stderr)}",
            retryable=True,
        )
