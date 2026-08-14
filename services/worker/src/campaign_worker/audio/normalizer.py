import json
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.video.ffmpeg_runner import run_ffmpeg_capturing_stderr

# Typical mobile/social video loudness target. The real Luna voiceover
# measured -24.5 LUFS with ~8 dB of true-peak headroom (-8.0 dBTP), so
# raising to -16 LUFS is safe with no clipping risk.
TARGET_INTEGRATED_LUFS = -16.0
TRUE_PEAK_CEILING_DBTP = -1.5
LOUDNESS_RANGE_LU = 11.0
_OUTPUT_BITRATE = "48k"

# loudnorm's single-pass stats block is a flat (non-nested) JSON object
# printed to stderr, e.g. {"input_i": "-24.53", ..., "output_i": "-16.01", ...}
_STATS_JSON_PATTERN = re.compile(r'\{[^{}]*"input_i"[^{}]*\}', re.DOTALL)


@dataclass(frozen=True, slots=True)
class NormalizedLoudness:
    data: bytes
    measured_integrated_lufs: float
    measured_true_peak_dbtp: float


class AudioNormalizer:
    """Applies one-pass ffmpeg loudnorm to synthesized voiceover audio.

    Runs before AudioProcessor.validate() and before the artifact reaches
    S3, so every downstream consumer (video render, which is a pure
    passthrough on the audio stream) receives correctly-leveled audio.
    """

    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        timeout_seconds: float = 30,
        target_lufs: float = TARGET_INTEGRATED_LUFS,
        true_peak_ceiling_dbtp: float = TRUE_PEAK_CEILING_DBTP,
        loudness_range_lu: float = LOUDNESS_RANGE_LU,
        ffmpeg_runner: Callable[..., Awaitable[str]] = run_ffmpeg_capturing_stderr,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._timeout_seconds = timeout_seconds
        self._target_lufs = target_lufs
        self._true_peak_ceiling_dbtp = true_peak_ceiling_dbtp
        self._loudness_range_lu = loudness_range_lu
        self._ffmpeg_runner = ffmpeg_runner

    @property
    def target_lufs(self) -> float:
        return self._target_lufs

    @property
    def true_peak_ceiling_dbtp(self) -> float:
        return self._true_peak_ceiling_dbtp

    async def normalize(self, mp3_bytes: bytes) -> NormalizedLoudness:
        with tempfile.TemporaryDirectory(prefix="c9-normalize-") as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.mp3"
            output_path = tmp_path / "normalized.mp3"
            input_path.write_bytes(mp3_bytes)

            loudnorm = (
                f"loudnorm=I={self._target_lufs}:TP={self._true_peak_ceiling_dbtp}:"
                f"LRA={self._loudness_range_lu}:print_format=json"
            )
            args = [
                "-y",
                "-i",
                str(input_path),
                "-af",
                loudnorm,
                "-c:a",
                "libmp3lame",
                "-b:a",
                _OUTPUT_BITRATE,
                str(output_path),
            ]
            stderr_text = await self._ffmpeg_runner(
                self._ffmpeg_path,
                args,
                timeout_seconds=self._timeout_seconds,
                unavailable_code="VOICE_PROVIDER_UNAVAILABLE",
            )
            measured_i, measured_tp = self._parse_stats(stderr_text)

            if not output_path.exists() or output_path.stat().st_size == 0:
                raise WorkflowOperationError(
                    "INVALID_PROVIDER_OUTPUT", "audio normalization produced no output", retryable=True
                )
            return NormalizedLoudness(
                data=output_path.read_bytes(),
                measured_integrated_lufs=measured_i,
                measured_true_peak_dbtp=measured_tp,
            )

    @staticmethod
    def _parse_stats(stderr_text: str) -> tuple[float, float]:
        match = _STATS_JSON_PATTERN.search(stderr_text)
        if match is None:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "could not locate loudnorm measurement stats", retryable=True
            )
        try:
            stats = json.loads(match.group(0))
            return float(stats["output_i"]), float(stats["output_tp"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "loudnorm measurement stats were malformed", retryable=True
            ) from exc
