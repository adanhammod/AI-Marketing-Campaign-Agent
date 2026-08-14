import asyncio
import sys
import textwrap
from pathlib import Path

import pytest

from campaign_worker.audio.normalizer import AudioNormalizer
from campaign_worker.errors import WorkflowOperationError
from campaign_worker.video.ffmpeg_runner import run_ffmpeg_capturing_stderr

_STATS_STDERR = """
[Parsed_loudnorm_0 @ 0x0]
{
\t"input_i" : "-24.53",
\t"input_tp" : "-7.97",
\t"input_lra" : "0.90",
\t"input_thresh" : "-34.78",
\t"output_i" : "-16.20",
\t"output_tp" : "-1.50",
\t"output_lra" : "1.20",
\t"output_thresh" : "-26.44",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.20"
}
"""


def _fake_binary(tmp_path, script: str) -> str:
    path = tmp_path / "fake_ffmpeg.py"
    path.write_text(textwrap.dedent(script))
    return str(path)


class _RecordingRunner:
    """Fake ffmpeg_runner: records invocation args, returns canned stderr."""

    def __init__(self, stderr_text: str = _STATS_STDERR, error: Exception | None = None) -> None:
        self.stderr_text = stderr_text
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, ffmpeg_path, args, *, timeout_seconds, unavailable_code="VIDEO_PROVIDER_UNAVAILABLE"):
        self.calls.append(
            {
                "ffmpeg_path": ffmpeg_path,
                "args": args,
                "timeout_seconds": timeout_seconds,
                "unavailable_code": unavailable_code,
            }
        )
        if self.error:
            raise self.error
        # Write real bytes to the output path (last positional arg) so
        # normalize()'s output-file existence check succeeds.
        await asyncio.to_thread(Path(args[-1]).write_bytes, b"ID3" + b"\x00" * 50)
        return self.stderr_text


@pytest.mark.asyncio
async def test_normalize_invocation_includes_loudnorm_filter_with_configured_targets():
    runner = _RecordingRunner()
    normalizer = AudioNormalizer(ffmpeg_runner=runner)
    await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    args = runner.calls[0]["args"]
    assert "-af" in args
    loudnorm_arg = args[args.index("-af") + 1]
    assert "loudnorm=" in loudnorm_arg
    assert "I=-16.0" in loudnorm_arg
    assert "TP=-1.5" in loudnorm_arg
    assert "LRA=11.0" in loudnorm_arg
    assert "print_format=json" in loudnorm_arg
    assert "-c:a" in args
    assert args[args.index("-c:a") + 1] == "libmp3lame"


@pytest.mark.asyncio
async def test_normalize_passes_voice_provider_unavailable_as_the_unavailable_code():
    runner = _RecordingRunner()
    normalizer = AudioNormalizer(ffmpeg_runner=runner)
    await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert runner.calls[0]["unavailable_code"] == "VOICE_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_normalize_returns_measured_stats_from_loudnorm_output():
    normalizer = AudioNormalizer(ffmpeg_runner=_RecordingRunner())
    result = await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert result.measured_integrated_lufs == -16.20
    assert result.measured_true_peak_dbtp == -1.50
    assert result.data.startswith(b"ID3")


@pytest.mark.asyncio
async def test_normalize_true_peak_stays_at_or_below_configured_ceiling():
    normalizer = AudioNormalizer(ffmpeg_runner=_RecordingRunner(), true_peak_ceiling_dbtp=-1.5)
    result = await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert result.measured_true_peak_dbtp <= -1.5


@pytest.mark.asyncio
async def test_normalize_quiet_input_measurably_increases_in_loudness():
    # A synthetic "quiet" source (represented here by a canned stderr with a
    # very low input_i) must come out measurably louder after normalization.
    quiet_stats = _STATS_STDERR.replace('"input_i" : "-24.53"', '"input_i" : "-30.00"')
    normalizer = AudioNormalizer(ffmpeg_runner=_RecordingRunner(stderr_text=quiet_stats))
    result = await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert result.measured_integrated_lufs > -30.00


@pytest.mark.asyncio
async def test_normalize_missing_stats_json_maps_to_invalid_provider_output():
    runner = _RecordingRunner(stderr_text="ffmpeg progress output with no stats block")
    normalizer = AudioNormalizer(ffmpeg_runner=runner)
    with pytest.raises(WorkflowOperationError) as error:
        await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_normalize_malformed_stats_json_maps_to_invalid_provider_output():
    runner = _RecordingRunner(stderr_text='{"input_i" : BROKEN-NOT-JSON}')
    normalizer = AudioNormalizer(ffmpeg_runner=runner)
    with pytest.raises(WorkflowOperationError) as error:
        await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_normalize_missing_output_file_maps_to_invalid_provider_output():
    class _NoOutputRunner:
        async def __call__(self, ffmpeg_path, args, *, timeout_seconds, unavailable_code="VIDEO_PROVIDER_UNAVAILABLE"):
            return _STATS_STDERR  # valid stats, but never writes the output file

    normalizer = AudioNormalizer(ffmpeg_runner=_NoOutputRunner())
    with pytest.raises(WorkflowOperationError) as error:
        await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_normalize_propagates_runner_errors_without_fallback():
    runner = _RecordingRunner(error=WorkflowOperationError("PROVIDER_TIMEOUT", "ffmpeg timed out", retryable=True))
    normalizer = AudioNormalizer(ffmpeg_runner=runner)
    with pytest.raises(WorkflowOperationError) as error:
        await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True


# ---------------------------------------------------------------------------
# Real asyncio subprocess mechanics (no injected fake), using the Python
# interpreter itself as a stand-in "ffmpeg" binary -- mirrors
# test_c8_video.py's ffmpeg_runner tests, so CI doesn't need real ffmpeg.
# The fake script is prepended to AudioNormalizer's own built args, since
# sys.executable needs a script path as argv[0] of the args list.
# ---------------------------------------------------------------------------


def _script_prepending_runner(script: str):
    async def runner(ffmpeg_path, args, *, timeout_seconds, unavailable_code="VIDEO_PROVIDER_UNAVAILABLE"):
        return await run_ffmpeg_capturing_stderr(
            ffmpeg_path, [script, *args], timeout_seconds=timeout_seconds, unavailable_code=unavailable_code
        )

    return runner


@pytest.mark.asyncio
async def test_normalize_missing_binary_maps_to_voice_provider_unavailable_not_retryable():
    normalizer = AudioNormalizer(ffmpeg_path="/nonexistent/ffmpeg-binary-xyz", timeout_seconds=5)
    with pytest.raises(WorkflowOperationError) as error:
        await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert error.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_normalize_non_zero_exit_maps_to_voice_provider_unavailable_retryable(tmp_path):
    script = _fake_binary(tmp_path, "import sys; sys.exit(1)")
    normalizer = AudioNormalizer(
        ffmpeg_path=sys.executable, timeout_seconds=5, ffmpeg_runner=_script_prepending_runner(script)
    )
    with pytest.raises(WorkflowOperationError) as error:
        await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert error.value.code == "VOICE_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_normalize_timeout_maps_to_provider_timeout(tmp_path):
    script = _fake_binary(tmp_path, "import time; time.sleep(30)")
    normalizer = AudioNormalizer(
        ffmpeg_path=sys.executable, timeout_seconds=0.2, ffmpeg_runner=_script_prepending_runner(script)
    )
    with pytest.raises(WorkflowOperationError) as error:
        await normalizer.normalize(b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True
