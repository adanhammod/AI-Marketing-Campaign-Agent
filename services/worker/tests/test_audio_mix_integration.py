"""Real-FFmpeg integration test, deliberately separate from the mocked unit
suite in test_audio_mix.py -- everything here shells out to a real binary.
Uses locally synthesized ffmpeg/lavfi tone audio purely as a deterministic
test fixture for duration/fade/mix/SFX-timing/clipping assertions; this is
not the production music strategy. Skips automatically when ffmpeg/ffprobe
are not installed."""

import asyncio
import shutil
import subprocess

import pytest

from campaign_worker.video.audio_mix import AudioMixRequest, SfxCue, build_audio_track
from campaign_worker.video.ffmpeg_runner import run_ffprobe

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are not installed in this environment",
)


async def _make_tone(path, *, duration: float, frequency: int = 440) -> None:
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}", str(path)],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_real_ffmpeg_mixes_music_and_sfx_into_a_duration_matched_track(tmp_path):
    music_path = tmp_path / "music.wav"
    sfx_path = tmp_path / "whoosh.wav"
    await _make_tone(music_path, duration=20.0, frequency=220)
    await _make_tone(sfx_path, duration=0.5, frequency=880)

    output_path = tmp_path / "mixed.m4a"
    request = AudioMixRequest(
        duration_seconds=6.0,
        music_path=music_path,
        voiceover_path=None,
        sfx_cues=[SfxCue(path=sfx_path, start_seconds=3.0)],
    )

    await build_audio_track(request, output_path, timeout_seconds=30)

    assert output_path.exists()
    assert output_path.stat().st_size > 0

    probe = await run_ffprobe("ffprobe", str(output_path), timeout_seconds=10)
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    assert len(audio_streams) == 1
    assert audio_streams[0]["codec_name"] == "aac"
    assert audio_streams[0]["sample_rate"] == "48000"
    assert abs(float(probe["format"]["duration"]) - 6.0) < 0.15


@pytest.mark.asyncio
async def test_real_ffmpeg_mixed_track_peak_stays_below_zero_dbfs(tmp_path):
    music_path = tmp_path / "music.wav"
    sfx_path = tmp_path / "whoosh.wav"
    await _make_tone(music_path, duration=10.0, frequency=220)
    await _make_tone(sfx_path, duration=1.0, frequency=880)

    output_path = tmp_path / "mixed.m4a"
    request = AudioMixRequest(
        duration_seconds=8.0,
        music_path=music_path,
        voiceover_path=None,
        sfx_cues=[SfxCue(path=sfx_path, start_seconds=2.0), SfxCue(path=sfx_path, start_seconds=5.0)],
    )

    await build_audio_track(request, output_path, timeout_seconds=30)

    result = await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-i", str(output_path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    max_volume_lines = [line for line in result.stderr.splitlines() if "max_volume" in line]
    assert max_volume_lines, "volumedetect did not report max_volume"
    max_volume_db = float(max_volume_lines[0].split(":")[-1].strip().replace(" dB", ""))
    assert max_volume_db < 0.0


@pytest.mark.asyncio
async def test_real_ffmpeg_short_music_loops_to_fill_the_full_target_duration(tmp_path):
    # Music is 2s, target is 9s -- without looping, ffmpeg would either end
    # early or pad with silence; build_audio_mix_args must loop the input so
    # the output is genuinely music for its full 9s, with no trailing gap.
    music_path = tmp_path / "short-music.wav"
    await _make_tone(music_path, duration=2.0, frequency=220)

    output_path = tmp_path / "mixed.m4a"
    request = AudioMixRequest(duration_seconds=9.0, music_path=music_path, voiceover_path=None)

    await build_audio_track(request, output_path, timeout_seconds=30)

    probe = await run_ffprobe("ffprobe", str(output_path), timeout_seconds=10)
    assert abs(float(probe["format"]["duration"]) - 9.0) < 0.15

    # Confirm audible signal (not silence) right at the very end of the clip,
    # where an unlooped 2s source would have already run out.
    result = await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-i", str(output_path), "-ss", "8.5", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    mean_volume_lines = [line for line in result.stderr.splitlines() if "mean_volume" in line]
    assert mean_volume_lines, "volumedetect did not report mean_volume for the tail segment"
    mean_volume_db = float(mean_volume_lines[0].split(":")[-1].strip().replace(" dB", ""))
    assert mean_volume_db > -90.0  # meaningfully above digital silence


@pytest.mark.asyncio
async def test_real_ffmpeg_voiceover_passthrough_preserves_its_own_duration(tmp_path):
    voiceover_path = tmp_path / "voiceover.wav"
    await _make_tone(voiceover_path, duration=4.0)

    output_path = tmp_path / "mixed.m4a"
    request = AudioMixRequest(duration_seconds=4.0, music_path=None, voiceover_path=voiceover_path)

    await build_audio_track(request, output_path, timeout_seconds=30)

    probe = await run_ffprobe("ffprobe", str(output_path), timeout_seconds=10)
    assert abs(float(probe["format"]["duration"]) - 4.0) < 0.15
