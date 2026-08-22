from pathlib import Path

import pytest

from campaign_worker.audio.tempo_normalizer import (
    MAX_TEMPO_FACTOR,
    MIN_TEMPO_FACTOR,
    ensure_duration_in_range,
)
from campaign_worker.errors import WorkflowOperationError

_MP3_BYTES = b"\xff\xfb\x90\x00" + b"\x00" * 100


class _FakeFfprobeRunner:
    def __init__(self, durations: list[float]) -> None:
        self._durations = list(durations)
        self.calls: list[str] = []

    async def __call__(self, ffprobe_path, file_path, *, timeout_seconds, extra_args=None):
        self.calls.append(str(file_path))
        duration = self._durations.pop(0) if len(self._durations) > 1 else self._durations[0]
        return {"format": {"duration": str(duration)}, "streams": [{"codec_type": "audio", "codec_name": "mp3"}]}


class _FakeFfmpegRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, ffmpeg_path, args, *, timeout_seconds, unavailable_code="VIDEO_PROVIDER_UNAVAILABLE"):
        self.calls.append(args)
        input_path = args[args.index("-i") + 1]
        output_path = args[-1]
        Path(output_path).write_bytes(Path(input_path).read_bytes())


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [13.0, 15.0, 17.4, 19.5, 20.0])
async def test_in_range_durations_pass_through_unchanged(duration):
    ffprobe = _FakeFfprobeRunner([duration])
    ffmpeg = _FakeFfmpegRunner()

    result = await ensure_duration_in_range(
        _MP3_BYTES,
        min_duration_seconds=13.0,
        max_duration_seconds=20.0,
        ffmpeg_runner=ffmpeg,
        ffprobe_runner=ffprobe,
    )

    assert result.data == _MP3_BYTES
    assert result.measured_duration_seconds == duration
    assert result.tempo_factor is None
    assert ffmpeg.calls == []


@pytest.mark.asyncio
async def test_slightly_above_max_gets_bounded_speed_up_and_succeeds():
    ffprobe = _FakeFfprobeRunner([20.4, 19.9])
    ffmpeg = _FakeFfmpegRunner()

    result = await ensure_duration_in_range(
        _MP3_BYTES,
        min_duration_seconds=13.0,
        max_duration_seconds=20.0,
        ffmpeg_runner=ffmpeg,
        ffprobe_runner=ffprobe,
    )

    assert len(ffmpeg.calls) == 1
    applied = ffmpeg.calls[0][ffmpeg.calls[0].index("-af") + 1]
    assert applied.startswith("atempo=")
    factor = float(applied.removeprefix("atempo="))
    assert MIN_TEMPO_FACTOR <= factor <= MAX_TEMPO_FACTOR
    assert factor > 1.0  # sped up to shorten
    assert result.tempo_factor == pytest.approx(factor)
    assert result.measured_duration_seconds == 19.9


@pytest.mark.asyncio
async def test_slightly_below_min_gets_bounded_slow_down_and_succeeds():
    ffprobe = _FakeFfprobeRunner([12.6, 13.1])
    ffmpeg = _FakeFfmpegRunner()

    result = await ensure_duration_in_range(
        _MP3_BYTES,
        min_duration_seconds=13.0,
        max_duration_seconds=20.0,
        ffmpeg_runner=ffmpeg,
        ffprobe_runner=ffprobe,
    )

    assert len(ffmpeg.calls) == 1
    applied = ffmpeg.calls[0][ffmpeg.calls[0].index("-af") + 1]
    factor = float(applied.removeprefix("atempo="))
    assert MIN_TEMPO_FACTOR <= factor <= MAX_TEMPO_FACTOR
    assert factor < 1.0  # slowed down to lengthen
    assert result.measured_duration_seconds == 13.1


@pytest.mark.asyncio
async def test_far_too_long_is_rejected_not_aggressively_stretched():
    ffprobe = _FakeFfprobeRunner([22.0])
    ffmpeg = _FakeFfmpegRunner()

    with pytest.raises(WorkflowOperationError) as error:
        await ensure_duration_in_range(
            _MP3_BYTES,
            min_duration_seconds=13.0,
            max_duration_seconds=20.0,
            ffmpeg_runner=ffmpeg,
            ffprobe_runner=ffprobe,
        )

    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert error.value.retryable is False
    assert ffmpeg.calls == []  # rejected before any ffmpeg mutation was attempted


@pytest.mark.asyncio
async def test_far_too_short_is_rejected_not_aggressively_stretched():
    ffprobe = _FakeFfprobeRunner([4.0])
    ffmpeg = _FakeFfmpegRunner()

    with pytest.raises(WorkflowOperationError) as error:
        await ensure_duration_in_range(
            _MP3_BYTES,
            min_duration_seconds=13.0,
            max_duration_seconds=20.0,
            ffmpeg_runner=ffmpeg,
            ffprobe_runner=ffprobe,
        )

    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert ffmpeg.calls == []


@pytest.mark.asyncio
async def test_still_out_of_range_after_correction_is_rejected_defensively():
    # Re-probe after applying the correction defends against ffmpeg's real
    # output landing outside range despite the pre-flight bound check.
    ffprobe = _FakeFfprobeRunner([20.4, 21.0])
    ffmpeg = _FakeFfmpegRunner()

    with pytest.raises(WorkflowOperationError) as error:
        await ensure_duration_in_range(
            _MP3_BYTES,
            min_duration_seconds=13.0,
            max_duration_seconds=20.0,
            ffmpeg_runner=ffmpeg,
            ffprobe_runner=ffprobe,
        )

    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert len(ffmpeg.calls) == 1  # the mutation was attempted, just didn't land in range
