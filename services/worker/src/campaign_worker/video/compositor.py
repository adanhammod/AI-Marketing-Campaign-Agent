from collections.abc import Sequence
from pathlib import Path


def build_render_args(
    *,
    scene_image_paths: Sequence[Path | str],
    scene_durations: Sequence[float],
    audio_path: Path | str,
    output_path: Path | str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    zoom_end: float = 1.06,
    fade_seconds: float = 0.3,
    crf: int = 20,
    preset: str = "veryfast",
    audio_bitrate: str = "128k",
    audio_sample_rate: int = 48000,
) -> list[str]:
    if not scene_image_paths or len(scene_image_paths) != len(scene_durations):
        raise ValueError("scene image paths and durations must be the same non-empty length")

    scene_count = len(scene_image_paths)
    args: list[str] = ["-y"]
    for path, duration in zip(scene_image_paths, scene_durations, strict=True):
        args += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(path)]
    args += ["-i", str(audio_path)]

    chains: list[str] = []
    for index, duration in enumerate(scene_durations):
        frames = max(1, round(duration * fps))
        zoom_increment = (zoom_end - 1.0) / max(frames - 1, 1)
        zoom_expr = f"min(zoom+{zoom_increment:.6f},{zoom_end})"
        chain = f"[{index}:v]scale={width}:{height},zoompan=z='{zoom_expr}':d={frames}:s={width}x{height}:fps={fps}"
        # zoompan's `d` does not itself bound this chain's output frame count
        # when fed a `-loop 1 -t <duration>` input -- without an explicit
        # trim, a scene's chain can run far past its intended length, and
        # concat (which drains each input stream in order before advancing)
        # never reaches the later scenes. setpts resets the trimmed segment
        # back to a zero-based timeline, which concat expects of every input.
        chain += f",trim=duration={duration:.3f},setpts=PTS-STARTPTS"
        if index == 0:
            chain += f",fade=t=in:st=0:d={fade_seconds}"
        if index == scene_count - 1:
            fade_start = max(duration - fade_seconds, 0)
            chain += f",fade=t=out:st={fade_start:.3f}:d={fade_seconds}"
        chains.append(f"{chain}[v{index}]")

    concat_inputs = "".join(f"[v{index}]" for index in range(scene_count))
    chains.append(f"{concat_inputs}concat=n={scene_count}:v=1:a=0[outv]")
    filter_complex = ";".join(chains)

    args += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        f"{scene_count}:a",
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ar",
        str(audio_sample_rate),
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_path),
    ]
    return args
