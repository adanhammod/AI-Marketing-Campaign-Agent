import pytest
from campaign_contracts.campaign import CreativeVideoPlan, VideoShot
from campaign_contracts.enums import AssetRole, AudioCueType, CameraMotion, ShotRole, TransitionType

from campaign_worker.video.creative_plan_adapter import (
    SUPPORTED_TRANSITIONS,
    UnsupportedTransitionError,
    build_resolved_shots,
    build_text_cues,
    resolve_camera_motion,
    resolve_transition,
    scale_resolved_shots,
)
from campaign_worker.video.models import ResolvedVideoShot, TextCue


def _shot(
    number,
    *,
    scene=1,
    duration=2.0,
    text=None,
    camera=CameraMotion.STATIC,
    transition=TransitionType.CUT,
    audio_cues=None,
) -> VideoShot:
    return VideoShot(
        shot_number=number,
        role=ShotRole.HOOK,
        source_scene_number=scene,
        asset_role=AssetRole.HERO_PRODUCT,
        visual_description="test shot",
        duration_seconds=duration,
        text=text,
        camera_motion=camera,
        transition_in=transition,
        audio_cues=audio_cues or [],
    )


def _plan(shots: list[VideoShot]) -> CreativeVideoPlan:
    return CreativeVideoPlan(
        concept="Test concept",
        visual_style="modern",
        total_duration_seconds=round(sum(s.duration_seconds for s in shots)),
        shots=shots,
    )


_ALL_CAMERA_MOTIONS = list(CameraMotion)
_SUPPORTED_TRANSITIONS = [TransitionType.CUT, TransitionType.MASK_REVEAL, TransitionType.CROSSFADE]
_UNSUPPORTED_TRANSITIONS = [TransitionType.WIPE, TransitionType.SCALE_THROUGH, TransitionType.MOTION_MATCH]


@pytest.mark.parametrize("motion", _ALL_CAMERA_MOTIONS)
def test_resolve_camera_motion_covers_every_enum_member(motion):
    key = resolve_camera_motion(motion)
    assert isinstance(key, str) and key


def test_resolve_camera_motion_keys_are_distinct():
    keys = {motion: resolve_camera_motion(motion) for motion in _ALL_CAMERA_MOTIONS}
    assert len(set(keys.values())) == len(_ALL_CAMERA_MOTIONS)


@pytest.mark.parametrize("transition", _SUPPORTED_TRANSITIONS)
def test_resolve_transition_supports_cut_mask_reveal_crossfade(transition):
    key = resolve_transition(transition)
    assert isinstance(key, str) and key


@pytest.mark.parametrize("transition", _UNSUPPORTED_TRANSITIONS)
def test_resolve_transition_raises_clearly_for_unsupported_values(transition):
    with pytest.raises(UnsupportedTransitionError):
        resolve_transition(transition)


def test_build_resolved_shots_is_deterministic():
    plan = _plan([_shot(1), _shot(2, scene=2), _shot(3, scene=3)])
    assert build_resolved_shots(plan) == build_resolved_shots(plan)


def test_build_resolved_shots_preserves_order_and_durations():
    plan = _plan([_shot(1, duration=1.5), _shot(2, scene=2, duration=2.5), _shot(3, scene=3, duration=3.0)])
    resolved = build_resolved_shots(plan)
    assert [s.shot_number for s in resolved] == [1, 2, 3]
    assert [s.duration_seconds for s in resolved] == [1.5, 2.5, 3.0]


def test_build_resolved_shots_resolves_source_scene_number_to_scene_number():
    plan = _plan([_shot(1, scene=1), _shot(2, scene=1), _shot(3, scene=2)])
    resolved = build_resolved_shots(plan)
    assert [s.scene_number for s in resolved] == [1, 1, 2]


def test_build_resolved_shots_maps_camera_and_transition_keys():
    plan = _plan([_shot(1, camera=CameraMotion.PUSH_IN, transition=TransitionType.MASK_REVEAL)])
    [resolved] = build_resolved_shots(plan)
    assert resolved.camera_motion_key == resolve_camera_motion(CameraMotion.PUSH_IN)
    assert resolved.transition_key == resolve_transition(TransitionType.MASK_REVEAL)


def test_build_resolved_shots_raises_for_unsupported_transition():
    plan = _plan([_shot(1, transition=TransitionType.MOTION_MATCH)])
    with pytest.raises(UnsupportedTransitionError):
        build_resolved_shots(plan)


def test_scale_resolved_shots_scales_every_duration_proportionally():
    shots = [
        ResolvedVideoShot(1, 1, 2.0, None, "static", "cut"),
        ResolvedVideoShot(2, 2, 3.0, None, "static", "cut"),
    ]
    scaled = scale_resolved_shots(shots, target_total_seconds=10.0)
    assert [s.duration_seconds for s in scaled] == [4.0, 6.0]


def test_scale_resolved_shots_preserves_order_text_and_keys():
    shots = [ResolvedVideoShot(1, 1, 2.0, "Hello", "push_in", "cut")]
    [scaled] = scale_resolved_shots(shots, target_total_seconds=1.0)
    assert scaled.shot_number == 1
    assert scaled.text == "Hello"
    assert scaled.camera_motion_key == "push_in"
    assert scaled.transition_key == "cut"


def test_scale_resolved_shots_with_matching_target_is_a_noop():
    shots = [
        ResolvedVideoShot(1, 1, 2.0, None, "static", "cut"),
        ResolvedVideoShot(2, 2, 3.0, None, "static", "cut"),
    ]
    scaled = scale_resolved_shots(shots, target_total_seconds=5.0)
    assert [s.duration_seconds for s in scaled] == [2.0, 3.0]


def test_build_text_cues_computes_cumulative_start_times():
    shots = [
        ResolvedVideoShot(1, 1, 2.0, "First", "static", "cut"),
        ResolvedVideoShot(2, 2, 3.0, None, "static", "cut"),
        ResolvedVideoShot(3, 3, 1.5, "Third", "static", "cut"),
    ]
    cues = build_text_cues(shots)
    assert cues == [
        TextCue(text="First", start_seconds=0.0, duration_seconds=2.0),
        TextCue(text="Third", start_seconds=5.0, duration_seconds=1.5),
    ]


def test_build_text_cues_skips_shots_with_no_text():
    shots = [ResolvedVideoShot(1, 1, 2.0, None, "static", "cut")]
    assert build_text_cues(shots) == []


def test_build_resolved_shots_carries_audio_cues_through():
    plan = _plan([_shot(1, audio_cues=[AudioCueType.TRANSITION_HIT, AudioCueType.BRAND_HIT])])
    [resolved] = build_resolved_shots(plan)
    assert resolved.audio_cues == (AudioCueType.TRANSITION_HIT, AudioCueType.BRAND_HIT)


def test_build_resolved_shots_audio_cues_empty_tuple_when_shot_has_none():
    plan = _plan([_shot(1)])
    [resolved] = build_resolved_shots(plan)
    assert resolved.audio_cues == ()


def test_supported_transitions_matches_renderer_resolvable_set():
    assert SUPPORTED_TRANSITIONS == {TransitionType.CUT, TransitionType.MASK_REVEAL, TransitionType.CROSSFADE}
    for transition in SUPPORTED_TRANSITIONS:
        resolve_transition(transition)  # must not raise


def test_scale_resolved_shots_preserves_audio_cues():
    shots = [ResolvedVideoShot(1, 1, 2.0, None, "static", "cut", audio_cues=(AudioCueType.WHOOSH,))]
    [scaled] = scale_resolved_shots(shots, target_total_seconds=4.0)
    assert scaled.audio_cues == (AudioCueType.WHOOSH,)
