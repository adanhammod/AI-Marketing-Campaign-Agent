"""Translates a semantic CreativeVideoPlan into renderer-facing data.

CreativeVideoPlan (shared contract) carries creative INTENT only -- roles,
asset intent, camera/transition/audio-cue enums. This adapter is the one
place that resolves that intent into renderer-facing primitives (resolved
shot list, flattened TextCues, camera/transition *keys*) -- never actual
CSS/GSAP values, which stay entirely inside hyperframes_composition.py.
"""

from campaign_contracts.campaign import CreativeVideoPlan
from campaign_contracts.enums import CameraMotion, TransitionType

from .models import ResolvedVideoShot, TextCue

# CameraMotion -> renderer motion key. Every enum member must be mapped -- a
# missing entry here would mean a plan-driven shot renders with no defined
# motion at all, so completeness is enforced by a parametrized test.
_CAMERA_MOTION_KEYS: dict[CameraMotion, str] = {
    CameraMotion.STATIC: "static",
    CameraMotion.PUSH_IN: "push_in",
    CameraMotion.PULL_OUT: "pull_out",
    CameraMotion.PAN_LEFT: "pan_left",
    CameraMotion.PAN_RIGHT: "pan_right",
    CameraMotion.PAN_UP: "pan_up",
    CameraMotion.PAN_DOWN: "pan_down",
    CameraMotion.MACRO_PUSH: "macro_push",
    CameraMotion.SCALE_THROUGH: "scale_through",
}

# TransitionType -> renderer transition key. Deliberately partial: only the
# transitions the renderer actually implements this slice. A plan shot using
# an unmapped value fails clearly here rather than silently substituting a
# different transition (or rendering no transition at all).
_TRANSITION_KEYS: dict[TransitionType, str] = {
    TransitionType.CUT: "cut",
    TransitionType.MASK_REVEAL: "mask_reveal",
    TransitionType.CROSSFADE: "crossfade",
}

# Public: the renderer-supported subset of TransitionType. Single source of
# truth for any other layer (e.g. a generative CreativeVideoPlan provider)
# that needs to know which transitions are safe to allow before a plan is
# even persisted, rather than discovering it only at render time.
SUPPORTED_TRANSITIONS: frozenset[TransitionType] = frozenset(_TRANSITION_KEYS.keys())


class UnsupportedTransitionError(ValueError):
    pass


def resolve_camera_motion(motion: CameraMotion) -> str:
    return _CAMERA_MOTION_KEYS[motion]


def resolve_transition(transition: TransitionType) -> str:
    try:
        return _TRANSITION_KEYS[transition]
    except KeyError as exc:
        raise UnsupportedTransitionError(
            f"TransitionType.{transition.name} is not yet supported by the renderer"
        ) from exc


def build_resolved_shots(plan: CreativeVideoPlan) -> list[ResolvedVideoShot]:
    return [
        ResolvedVideoShot(
            shot_number=shot.shot_number,
            scene_number=shot.source_scene_number,
            # VideoShot.duration_seconds is Decimal (DynamoDB-persisted field);
            # every renderer-facing type downstream of this adapter is float,
            # so this is the one place that crosses the boundary explicitly.
            duration_seconds=float(shot.duration_seconds),
            text=shot.text,
            camera_motion_key=resolve_camera_motion(shot.camera_motion),
            transition_key=resolve_transition(shot.transition_in),
            audio_cues=tuple(shot.audio_cues),
        )
        for shot in plan.shots
    ]


def scale_resolved_shots(
    shots: list[ResolvedVideoShot], *, target_total_seconds: float
) -> list[ResolvedVideoShot]:
    original_total = sum(shot.duration_seconds for shot in shots)
    if original_total <= 0:
        raise ValueError("resolved shots must have a positive total duration")
    scale = target_total_seconds / original_total
    return [
        ResolvedVideoShot(
            shot_number=shot.shot_number,
            scene_number=shot.scene_number,
            duration_seconds=shot.duration_seconds * scale,
            text=shot.text,
            camera_motion_key=shot.camera_motion_key,
            transition_key=shot.transition_key,
            audio_cues=shot.audio_cues,
        )
        for shot in shots
    ]


def build_text_cues(shots: list[ResolvedVideoShot]) -> list[TextCue]:
    cues: list[TextCue] = []
    start = 0.0
    for shot in shots:
        if shot.text:
            cues.append(TextCue(text=shot.text, start_seconds=start, duration_seconds=shot.duration_seconds))
        start += shot.duration_seconds
    return cues
