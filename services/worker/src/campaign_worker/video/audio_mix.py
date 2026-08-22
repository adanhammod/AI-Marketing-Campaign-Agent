"""Builds the single final audio track handed to a video renderer.

Used uniformly across VideoStyles: a plain voiceover passthrough, a
voiceover+ducked-music mix (VOICEOVER_AD with music available), and a
music+SFX mixdown with no voiceover (CINEMATIC_TEXT_AD). The renderer
itself never needs to know which one produced the file it receives.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg_runner import run_ffmpeg

_FADE_SECONDS = 0.5

# Conservative, worker-internal mix levels -- never put implementation dB/gain
# values into shared contracts. Music is the main bed (unity gain, shaped only
# by its fade envelope) when it's the *only* track; SFX cues sit clearly
# under it so they accent transitions without competing with the bed for
# attention. When music is layered under a voiceover instead, it's ducked
# well below the bed level so the narration stays clearly legible.
_MUSIC_GAIN = 1.0
_MUSIC_DUCK_GAIN = 0.15
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

    has_voiceover = request.voiceover_path is not None
    has_music = request.music_path is not None
    duration = request.duration_seconds
    fade_out_start = max(duration - _FADE_SECONDS, 0.0)

    args: list[str] = ["-y"]
    filters: list[str] = []
    mix_labels: list[str] = []
    input_index = 0

    if has_voiceover and has_music:
        # Combined mix: the voiceover is the primary/legible track and is
        # never gain-reduced (only trimmed to the target duration -- the
        # complete narration is preserved); music is ducked well underneath
        # it, with the same fade in/out envelope a music-only bed gets.
        args += ["-i", str(request.voiceover_path)]
        filters.append(f"[{input_index}:a]atrim=0:{duration:.3f}[voice]")
        mix_labels.append("[voice]")
        input_index += 1

        args += ["-stream_loop", "-1", "-i", str(request.music_path)]
        filters.append(
            f"[{input_index}:a]volume={_MUSIC_DUCK_GAIN},atrim=0:{duration:.3f},"
            f"afade=t=in:d={_FADE_SECONDS},afade=t=out:st={fade_out_start:.3f}:d={_FADE_SECONDS}[music]"
        )
        mix_labels.append("[music]")
        input_index += 1
    else:
        # Exactly one primary track: a music bed (no voiceover) or a
        # voiceover passthrough (no music available) -- unchanged from
        # before combined mixing existed.
        primary_path = request.voiceover_path if has_voiceover else request.music_path
        is_music_bed = has_music  # equivalent to `not has_voiceover` in this branch
        if is_music_bed:
            # Loop the music input indefinitely; atrim below still cuts it to
            # the exact target duration, so this transparently covers both a
            # track shorter than the video (loops to fill it, leaving no
            # trailing silence) and one that's longer (the loop is
            # irrelevant -- atrim discards the excess either way).
            args += ["-stream_loop", "-1"]
        args += ["-i", str(primary_path)]
        input_index += 1
        if is_music_bed:
            filters.append(
                f"[0:a]volume={_MUSIC_GAIN},atrim=0:{duration:.3f},"
                f"afade=t=in:d={_FADE_SECONDS},afade=t=out:st={fade_out_start:.3f}:d={_FADE_SECONDS}[bed]"
            )
        else:
            filters.append(f"[0:a]atrim=0:{duration:.3f}[bed]")
        mix_labels.append("[bed]")

    for cue in request.sfx_cues:
        args += ["-i", str(cue.path)]
        delay_ms = round(cue.start_seconds * 1000)
        filters.append(f"[{input_index}:a]volume={_SFX_GAIN},adelay={delay_ms}|{delay_ms}[sfx{input_index}]")
        mix_labels.append(f"[sfx{input_index}]")
        input_index += 1

    if len(mix_labels) > 1:
        # normalize=0 only on the new combined voiceover+music path -- ffmpeg's
        # amix otherwise silently redivides every input's amplitude by the
        # input count, which would both unintentionally quiet the voiceover
        # and throw off the intended duck ratio. The existing music(+SFX)
        # amix path (no voiceover) is deliberately left without this flag,
        # unchanged from before, to keep CINEMATIC_TEXT_AD's output identical.
        normalize_arg = ":normalize=0" if (has_voiceover and has_music) else ""
        filters.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:"
            f"dropout_transition=0{normalize_arg}[out]"
        )
        final_label = "[out]"
    else:
        final_label = mix_labels[0]

    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        final_label,
        "-t",
        f"{duration:.3f}",
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
