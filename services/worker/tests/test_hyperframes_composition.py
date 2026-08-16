from pathlib import Path

from campaign_contracts.campaign import Storyboard, StoryboardScene

from campaign_worker.video.hyperframes_composition import build_composition_html
from campaign_worker.video.models import LocalRenderRequest, ResolvedVideoShot, TextCue


def _storyboard() -> Storyboard:
    return Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration=f"Narration for scene {n}.",
                visual_prompt=f"cold brew scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )


def _request(**overrides) -> LocalRenderRequest:
    defaults = dict(
        scene_image_paths={1: Path("/tmp/scene-1.jpg"), 2: Path("/tmp/scene-2.jpg"), 3: Path("/tmp/scene-3.jpg")},
        scene_durations={1: 5.0, 2: 5.0, 3: 5.0},
        audio_path=Path("/tmp/voiceover.mp3"),
        storyboard=_storyboard(),
        headline="Luna Cold Brew",
        key_message="Smooth energy for your day.",
        cta="Discover the collection.",
        output_path=Path("/tmp/final.mp4"),
        width=1080,
        height=1920,
        fps=30,
    )
    defaults.update(overrides)
    return LocalRenderRequest(**defaults)


def test_composition_declares_target_dimensions_and_fps():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert 'data-width="1080"' in html
    assert 'data-height="1920"' in html
    assert 'data-fps="30"' in html


def test_composition_references_all_three_scene_images():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert "scene-1.jpg" in html
    assert "scene-2.jpg" in html
    assert "scene-3.jpg" in html


def test_composition_references_the_audio_track_spanning_the_full_duration():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert 'src="voiceover.mp3"' in html
    assert 'data-duration="15.000"' in html


def test_composition_contains_all_three_on_screen_text_moments_but_not_full_narration():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert "Luna Cold Brew" in html
    assert "Smooth energy for your day." in html
    assert "Discover the collection." in html
    assert "Narration for scene 1." not in html


def test_composition_produces_six_distinct_shots_from_three_source_images():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert html.count('class="shot"') == 6


def test_composition_assigns_two_shots_each_to_scenes_one_and_three():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert html.count("scene-1.jpg") == 2
    assert html.count("scene-3.jpg") == 2


def test_composition_uses_real_scene_durations_not_a_fixed_fifteen_seconds():
    # Regression guard: shot timings must derive from the real (audio-scaled)
    # scene durations, not a hardcoded 5s-per-scene assumption.
    html = build_composition_html(
        _request(scene_durations={1: 4.0, 2: 6.0, 3: 4.5}),
        image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"},
        audio_filename="voiceover.mp3",
    )
    assert 'data-duration="14.500"' in html


def test_composition_falls_back_to_storyboard_narration_when_copy_fields_are_empty():
    # Regression guard for the real stored Luna campaign, where
    # campaign_copy.headline/call_to_action are empty and the narration
    # itself already carries the intended on-screen phrasing.
    html = build_composition_html(
        _request(headline="", key_message="", cta=""),
        image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"},
        audio_filename="voiceover.mp3",
    )
    assert "Narration for scene 1." in html
    assert "Narration for scene 2." in html
    assert "Narration for scene 3." in html


def test_composition_uses_text_cues_when_provided_instead_of_headline_key_message_cta():
    request = _request(
        headline="Should not appear",
        key_message="Should not appear either",
        cta="Nor this",
        text_cues=[
            TextCue(text="YOUR 3PM RESET.", start_seconds=0.0, duration_seconds=1.5),
            TextCue(text="SMOOTH ENERGY.", start_seconds=5.0, duration_seconds=2.0),
        ],
    )
    html = build_composition_html(
        request, image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )

    assert "YOUR 3PM RESET." in html
    assert "SMOOTH ENERGY." in html
    assert "Should not appear" not in html
    assert "Nor this" not in html


def test_composition_text_cue_timing_uses_the_cues_own_start_and_duration():
    request = _request(
        text_cues=[TextCue(text="COLD. BOLD. READY.", start_seconds=8.25, duration_seconds=1.75)],
    )
    html = build_composition_html(
        request, image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )

    assert 'data-start="8.250"' in html
    assert 'data-duration="1.750"' in html


def test_composition_registers_a_gsap_timeline_for_camera_and_text_animation():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert "window.__timelines" in html


# ---------------------------------------------------------------------------
# Plan-driven path: LocalRenderRequest.resolved_shots becomes authoritative
# when present, instead of the legacy hardcoded 6-shot _build_shot_plan.
# ---------------------------------------------------------------------------


def _resolved_shots() -> list[ResolvedVideoShot]:
    return [
        ResolvedVideoShot(1, 1, 3.0, "HOOK LINE.", "push_in", "cut"),
        ResolvedVideoShot(2, 2, 4.0, None, "pan_left", "mask_reveal"),
        ResolvedVideoShot(3, 3, 2.0, "CTA LINE.", "static", "crossfade"),
    ]


def _text_cues_for_resolved_shots() -> list[TextCue]:
    # Mirrors what creative_plan_adapter.build_text_cues would produce for
    # _resolved_shots() -- composition tests stay independent of the adapter
    # module, exercising LocalRenderRequest inputs directly, as real pipeline
    # wiring would populate them.
    return [
        TextCue(text="HOOK LINE.", start_seconds=0.0, duration_seconds=3.0),
        TextCue(text="CTA LINE.", start_seconds=7.0, duration_seconds=2.0),
    ]


def test_composition_with_resolved_shots_produces_exactly_that_many_shots_not_the_legacy_six():
    html = build_composition_html(
        _request(resolved_shots=_resolved_shots(), text_cues=_text_cues_for_resolved_shots()),
        image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"},
        audio_filename="voiceover.mp3",
    )
    assert html.count('class="shot"') == 3


def test_composition_with_resolved_shots_uses_their_scene_and_duration_not_legacy_splits():
    html = build_composition_html(
        _request(resolved_shots=_resolved_shots(), text_cues=_text_cues_for_resolved_shots()),
        image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"},
        audio_filename="voiceover.mp3",
    )
    # Legacy plan would split scene 1 into two 2.5s shots (half of 5.0); the
    # resolved plan instead has one 3.0s shot on scene 1 -- proving the legacy
    # _build_shot_plan split was not used.
    assert 'data-duration="3.000"' in html
    assert html.count("scene-1.jpg") == 1
    assert html.count("scene-2.jpg") == 1
    assert html.count("scene-3.jpg") == 1


def test_composition_with_resolved_shots_computes_cumulative_start_times():
    html = build_composition_html(
        _request(resolved_shots=_resolved_shots(), text_cues=_text_cues_for_resolved_shots()),
        image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"},
        audio_filename="voiceover.mp3",
    )
    # shot 1 starts at 0.0 (duration 3.0), shot 2 starts at 3.0 (duration 4.0),
    # shot 3 starts at 7.0 (duration 2.0).
    assert 'data-start="0.000" data-duration="3.000"' in html
    assert 'data-start="3.000" data-duration="4.000"' in html
    assert 'data-start="7.000" data-duration="2.000"' in html


def test_composition_with_resolved_shots_uses_plan_text_not_headline_key_message_cta():
    html = build_composition_html(
        _request(
            headline="Should not appear",
            key_message="Should not appear either",
            cta="Nor this",
            resolved_shots=_resolved_shots(),
            text_cues=_text_cues_for_resolved_shots(),
        ),
        image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"},
        audio_filename="voiceover.mp3",
    )
    assert "HOOK LINE." in html
    assert "CTA LINE." in html
    assert "Should not appear" not in html
    assert "Nor this" not in html


def test_composition_without_resolved_shots_still_uses_legacy_six_shot_plan():
    html = build_composition_html(
        _request(), image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"}, audio_filename="voiceover.mp3"
    )
    assert html.count('class="shot"') == 6


def test_composition_with_resolved_shots_supports_mask_reveal_and_crossfade_transitions():
    html = build_composition_html(
        _request(resolved_shots=_resolved_shots(), text_cues=_text_cues_for_resolved_shots()),
        image_filenames={1: "scene-1.jpg", 2: "scene-2.jpg", 3: "scene-3.jpg"},
        audio_filename="voiceover.mp3",
    )
    # mask_reveal shot (shot-2) gets a wipe div; cut/crossfade shots do not.
    assert 'id="shot-2-wipe"' in html
    assert 'id="shot-1-wipe"' not in html
    assert 'id="shot-3-wipe"' not in html
    # crossfade shot (shot-3) fades in via opacity tween rather than a hard cut.
    assert 'tl.fromTo("#shot-3", {opacity: 0}' in html
