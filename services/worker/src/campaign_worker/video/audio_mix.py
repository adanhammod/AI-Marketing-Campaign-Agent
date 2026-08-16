"""Builds the single final audio track handed to a video renderer.

Used uniformly by both VideoStyles: a plain voiceover passthrough (today's
VOICEOVER_AD behavior) and a music+SFX mixdown (CINEMATIC_TEXT_AD). The
renderer itself never needs to know which one produced the file it receives.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg_runner import run_ffmpeg

_FADE_SECONDS = 0.5

# Conservative, worker-internal mix levels -- never put implementation dB/gain
# values into shared contracts. Music is the main bed (unity gain, shaped only
# by its fade envelope); SFX cues sit clearly under it so they accent
# transitions without competing with the bed for attention.
_MUSIC_GAIN = 1.0
_SFX_GAIN = 0.5


@dataclass(frozen=True, slots=True)
class SfxCue:
    path: Path
    start_seconds: float


@dataclass(frozen=True, slots=True)
class AudioMixRequest:
    duration_seconds: float
    music_path: Path | None
    voiceover_path: Path | None
    sfx_cues: list[SfxCue] = field(default_factory=list)


def build_audio_mix_args(request: AudioMixRequest, output_path: Path) -> list[str]:
    if request.voiceover_path is None and request.music_path is None:
        raise ValueError("audio mix requires at least a voiceover_path or a music_path")

    # A music bed gets faded in/out; a voiceover passthrough is used as-is.
    primary_path = request.voiceover_path if request.voiceover_path is not None else request.music_path
    is_music_bed = request.voiceover_path is None

    args: list[str] = ["-y"]
    if is_music_bed:
        # Loop the music input indefinitely; atrim below still cuts it to the
        # exact target duration, so this transparently covers both a track
        # shorter than the video (loops to fill it, leaving no trailing
        # silence) and one that's longer (the loop is irrelevant -- atrim
        # discards the excess either way).
        args += ["-stream_loop", "-1"]
    args += ["-i", str(primary_path)]
    for cue in request.sfx_cues:
        args += ["-i", str(cue.path)]

    filters: list[str] = []
    if is_music_bed:
        fade_out_start = max(request.duration_seconds - _FADE_SECONDS, 0.0)
        filters.append(
            f"[0:a]volume={_MUSIC_GAIN},atrim=0:{request.duration_seconds:.3f},"
            f"afade=t=in:d={_FADE_SECONDS},afade=t=out:st={fade_out_start:.3f}:d={_FADE_SECONDS}[bed]"
        )
    else:
        filters.append(f"[0:a]atrim=0:{request.duration_seconds:.3f}[bed]")

    mix_labels = ["[bed]"]
    for index, cue in enumerate(request.sfx_cues, start=1):
        delay_ms = round(cue.start_seconds * 1000)
        filters.append(f"[{index}:a]volume={_SFX_GAIN},adelay={delay_ms}|{delay_ms}[sfx{index}]")
        mix_labels.append(f"[sfx{index}]")

    if len(mix_labels) > 1:
        filters.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0[out]")
        final_label = "[out]"
    else:
        final_label = "[bed]"

    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        final_label,
        "-t",
        f"{request.duration_seconds:.3f}",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        str(output_path),
    ]
    return args


async def build_audio_track(
    request: AudioMixRequest,
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    timeout_seconds: float = 60,
    ffmpeg_runner: Callable[..., Awaitable[None]] = run_ffmpeg,
) -> Path:
    args = build_audio_mix_args(request, output_path)
    await ffmpeg_runner(ffmpeg_path, args, timeout_seconds=timeout_seconds)
    return output_path
