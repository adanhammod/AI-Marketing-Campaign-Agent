from pathlib import Path

import pytest

from campaign_worker.video.audio_mix import AudioMixRequest, SfxCue, build_audio_mix_args


def test_raises_when_neither_voiceover_nor_music_given():
    request = AudioMixRequest(duration_seconds=15.0, music_path=None, voiceover_path=None)
    with pytest.raises(ValueError, match="voiceover_path or.*music_path"):
        build_audio_mix_args(request, Path("/tmp/mixed.aac"))


def test_voiceover_passthrough_uses_voiceover_as_the_only_input_when_no_sfx():
    request = AudioMixRequest(
        duration_seconds=15.0, music_path=None, voiceover_path=Path("/tmp/voiceover.mp3")
    )
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))

    assert args.count("-i") == 1
    input_index = args.index("-i")
    assert args[input_index + 1] == "/tmp/voiceover.mp3"


def test_voiceover_passthrough_does_not_apply_fade():
    request = AudioMixRequest(
        duration_seconds=15.0, music_path=None, voiceover_path=Path("/tmp/voiceover.mp3")
    )
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "afade" not in filter_complex


def test_music_bed_applies_fade_in_and_fade_out():
    request = AudioMixRequest(duration_seconds=15.0, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "afade=t=in" in filter_complex
    assert "afade=t=out" in filter_complex


def test_sfx_cues_are_delayed_to_their_start_time_and_mixed_in():
    request = AudioMixRequest(
        duration_seconds=15.0,
        music_path=Path("/tmp/music.wav"),
        voiceover_path=None,
        sfx_cues=[SfxCue(path=Path("/tmp/whoosh.wav"), start_seconds=5.0)],
    )
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))

    assert args.count("-i") == 2
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "adelay=5000|5000" in filter_complex
    assert "amix=inputs=2" in filter_complex


def test_no_sfx_skips_amix_entirely():
    request = AudioMixRequest(duration_seconds=15.0, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "amix" not in filter_complex


def test_output_is_trimmed_to_duration_seconds():
    request = AudioMixRequest(duration_seconds=12.345, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))

    assert args[args.index("-t") + 1] == "12.345"


def test_encodes_aac_at_48k():
    request = AudioMixRequest(duration_seconds=15.0, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))

    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-ar") + 1] == "48000"


def test_output_path_is_the_final_argument():
    request = AudioMixRequest(duration_seconds=15.0, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))

    assert args[-1] == "/tmp/mixed.aac"


def test_deterministic_for_identical_inputs():
    request = AudioMixRequest(
        duration_seconds=15.0,
        music_path=Path("/tmp/music.wav"),
        voiceover_path=None,
        sfx_cues=[SfxCue(path=Path("/tmp/whoosh.wav"), start_seconds=5.0)],
    )
    assert build_audio_mix_args(request, Path("/tmp/mixed.aac")) == build_audio_mix_args(
        request, Path("/tmp/mixed.aac")
    )


# ---------------------------------------------------------------------------
# Music loop (short tracks) / trim (long tracks) + mix-level gain
# ---------------------------------------------------------------------------


def test_music_bed_input_loops_indefinitely_so_short_tracks_fill_the_target_duration():
    request = AudioMixRequest(duration_seconds=15.0, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))

    assert "-stream_loop" in args
    loop_index = args.index("-stream_loop")
    assert args[loop_index + 1] == "-1"
    # The loop flag must apply to the music input specifically, i.e. appear
    # immediately before that -i.
    assert args[loop_index + 2] == "-i"
    assert args[loop_index + 3] == "/tmp/music.wav"


def test_music_bed_is_still_trimmed_to_exact_target_duration_regardless_of_source_length():
    request = AudioMixRequest(duration_seconds=12.345, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "atrim=0:12.345" in filter_complex
    assert args[args.index("-t") + 1] == "12.345"


def test_voiceover_passthrough_input_does_not_loop():
    request = AudioMixRequest(
        duration_seconds=15.0, music_path=None, voiceover_path=Path("/tmp/voiceover.mp3")
    )
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    assert "-stream_loop" not in args


def test_music_bed_has_an_explicit_conservative_volume_filter():
    request = AudioMixRequest(duration_seconds=15.0, music_path=Path("/tmp/music.wav"), voiceover_path=None)
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "volume=" in filter_complex.split(",")[0]


def test_sfx_cues_are_gained_down_relative_to_the_music_bed_to_avoid_overpowering_it():
    request = AudioMixRequest(
        duration_seconds=15.0,
        music_path=Path("/tmp/music.wav"),
        voiceover_path=None,
        sfx_cues=[SfxCue(path=Path("/tmp/whoosh.wav"), start_seconds=5.0)],
    )
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    bed_chain, sfx_chain = filter_complex.split(";")[0], filter_complex.split(";")[1]
    bed_gain = float(bed_chain.split("volume=")[1].split(":")[0].split(",")[0])
    sfx_gain = float(sfx_chain.split("volume=")[1].split(":")[0].split(",")[0])
    assert sfx_gain < bed_gain
