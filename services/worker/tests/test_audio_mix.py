from pathlib import Path

import pytest

from campaign_worker.video.audio_mix import _MUSIC_DUCK_GAIN, AudioMixRequest, SfxCue, build_audio_mix_args


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


# ---------------------------------------------------------------------------
# Combined voiceover + music (VOICEOVER_AD with music available)
# ---------------------------------------------------------------------------


def _combined_request(**overrides) -> AudioMixRequest:
    defaults = dict(
        duration_seconds=15.0,
        music_path=Path("/tmp/music.wav"),
        voiceover_path=Path("/tmp/voiceover.mp3"),
    )
    defaults.update(overrides)
    return AudioMixRequest(**defaults)


def test_combined_mix_sends_both_voiceover_and_music_as_ffmpeg_inputs():
    args = build_audio_mix_args(_combined_request(), Path("/tmp/mixed.aac"))

    assert args.count("-i") == 2
    inputs = [args[i + 1] for i, token in enumerate(args) if token == "-i"]
    assert inputs == ["/tmp/voiceover.mp3", "/tmp/music.wav"]


def test_combined_mix_loops_the_music_input_but_not_the_voiceover_input():
    args = build_audio_mix_args(_combined_request(), Path("/tmp/mixed.aac"))

    assert args.count("-stream_loop") == 1
    loop_index = args.index("-stream_loop")
    assert args[loop_index + 1] == "-1"
    assert args[loop_index + 2] == "-i"
    assert args[loop_index + 3] == "/tmp/music.wav"


def test_combined_mix_voiceover_chain_has_no_gain_reduction():
    # "Preserve the complete voiceover" / "stays at normal level": the
    # voiceover's own filter chain must only trim to duration, never apply
    # a volume filter.
    args = build_audio_mix_args(_combined_request(), Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]
    voice_chain = next(chain for chain in filter_complex.split(";") if chain.endswith("[voice]"))

    assert voice_chain == "[0:a]atrim=0:15.000[voice]"
    assert "volume=" not in voice_chain


def test_combined_mix_music_is_ducked_to_the_configured_gain():
    args = build_audio_mix_args(_combined_request(), Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]
    music_chain = next(chain for chain in filter_complex.split(";") if chain.endswith("[music]"))

    assert f"volume={_MUSIC_DUCK_GAIN}" in music_chain
    assert _MUSIC_DUCK_GAIN == pytest.approx(0.15)
    assert 0.10 <= _MUSIC_DUCK_GAIN <= 0.20


def test_combined_mix_keeps_the_existing_fade_envelope_on_the_music_bed():
    args = build_audio_mix_args(_combined_request(duration_seconds=15.0), Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]
    music_chain = next(chain for chain in filter_complex.split(";") if chain.endswith("[music]"))

    assert "afade=t=in:d=0.5" in music_chain
    assert "afade=t=out:st=14.500:d=0.5" in music_chain
    # No fade on the voiceover chain.
    voice_chain = next(chain for chain in filter_complex.split(";") if chain.endswith("[voice]"))
    assert "afade" not in voice_chain


def test_combined_mix_uses_amix_with_normalize_disabled():
    args = build_audio_mix_args(_combined_request(), Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[out]" in filter_complex
    assert args[args.index("-map") + 1] == "[out]"


def test_music_only_amix_path_is_unaffected_by_normalize_flag():
    # Regression guard: CINEMATIC_TEXT_AD's existing music(+SFX) amix call
    # must not gain a normalize token as a side effect of the new combined
    # path -- its filter string stays exactly as it was before this change.
    request = AudioMixRequest(
        duration_seconds=15.0,
        music_path=Path("/tmp/music.wav"),
        voiceover_path=None,
        sfx_cues=[SfxCue(path=Path("/tmp/whoosh.wav"), start_seconds=5.0)],
    )
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "normalize" not in filter_complex
    assert "[bed][sfx1]amix=inputs=2:duration=first:dropout_transition=0[out]" in filter_complex


def test_voiceover_only_path_still_has_no_normalize_or_amix_token():
    request = AudioMixRequest(duration_seconds=15.0, music_path=None, voiceover_path=Path("/tmp/voiceover.mp3"))
    args = build_audio_mix_args(request, Path("/tmp/mixed.aac"))
    filter_complex = args[args.index("-filter_complex") + 1]

    assert "amix" not in filter_complex
    assert "normalize" not in filter_complex
