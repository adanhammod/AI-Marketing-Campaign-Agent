"""Builds a HyperFrames HTML composition from a LocalRenderRequest.

Turns the 3 source scene images into 6 virtual shots (2 per image) using
different crops/framing and reusable camera-motion primitives, rather than a
3-slide crossfade deck. Purely data-driven: no campaign-specific content is
hardcoded here, everything comes from the LocalRenderRequest.
"""

from dataclasses import dataclass

from .models import LocalRenderRequest, ResolvedVideoShot, TextCue

_COMPOSITION_ID = "campaign-video"

# Neutral default framing for plan-driven shots. CreativeVideoPlan carries no
# crop/framing detail yet (VideoShot.visual_description is semantic creative
# intent, not a CSS value) -- a future image/framing slice can translate that
# intent into per-shot crop/framing here without touching shared contracts.
_DEFAULT_CROP = "50% 50%"

# Reusable camera-motion primitives: (from-transform, to-transform) applied
# to a shot's .frame element across its own [start, start+duration] window.
_CAMERA_TRANSFORMS: dict[str, tuple[dict[str, float | str], dict[str, float | str]]] = {
    "static": ({"scale": 1.0}, {"scale": 1.0}),
    "push_in": ({"scale": 1.0}, {"scale": 1.14}),
    "pull_out": ({"scale": 1.14}, {"scale": 1.0}),
    "pan_left": ({"x": "2%", "scale": 1.08}, {"x": "-2%", "scale": 1.08}),
    "pan_right": ({"x": "-2%", "scale": 1.08}, {"x": "2%", "scale": 1.08}),
    "pan_up": ({"y": "2%", "scale": 1.08}, {"y": "-2%", "scale": 1.08}),
    "pan_down": ({"y": "-2%", "scale": 1.08}, {"y": "2%", "scale": 1.08}),
    "macro_push": ({"scale": 1.05}, {"scale": 1.28}),
    "scale_through": ({"scale": 1.0}, {"scale": 1.35}),
    "subtle_float": ({"y": "0%", "scale": 1.05}, {"y": "-1.5%", "scale": 1.08}),
}


@dataclass(frozen=True)
class _Shot:
    shot_id: str
    scene_number: int
    start: float
    duration: float
    camera: str
    crop: str  # CSS background-position
    transition_key: str  # "cut" | "mask_reveal" | "crossfade"


def _first_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    first, _, _ = text.partition(".")
    return f"{first.strip()}." if first.strip() else text


def _on_screen_text(request: LocalRenderRequest, scenes_by_number: dict[int, str]) -> tuple[str, str, str]:
    headline = request.headline.strip() or _first_sentence(scenes_by_number[1])
    key_message = request.key_message.strip() or _first_sentence(scenes_by_number[2])
    cta = request.cta.strip() or _first_sentence(scenes_by_number[3])
    return headline, key_message, cta


def _shots_from_resolved(resolved_shots: list[ResolvedVideoShot]) -> list[_Shot]:
    shots = []
    start = 0.0
    for shot in resolved_shots:
        shots.append(
            _Shot(
                f"shot-{shot.shot_number}",
                shot.scene_number,
                start,
                shot.duration_seconds,
                shot.camera_motion_key,
                _DEFAULT_CROP,
                transition_key=shot.transition_key,
            )
        )
        start += shot.duration_seconds
    return shots


def _legacy_shot_plan(request: LocalRenderRequest) -> list[_Shot]:
    d1 = request.scene_durations[1]
    d2 = request.scene_durations[2]
    d3 = request.scene_durations[3]
    scene1_start, scene2_start, scene3_start = 0.0, d1, d1 + d2

    return [
        _Shot("shot-1", 1, scene1_start, d1 * 0.5, "push_in", "50% 35%", transition_key="cut"),
        _Shot("shot-2", 1, scene1_start + d1 * 0.5, d1 * 0.5, "pull_out", "50% 55%", transition_key="cut"),
        _Shot("shot-3", 2, scene2_start, d2 * 0.45, "pan_left", "60% 45%", transition_key="mask_reveal"),
        _Shot("shot-4", 2, scene2_start + d2 * 0.45, d2 * 0.55, "subtle_float", "40% 50%", transition_key="cut"),
        _Shot("shot-5", 3, scene3_start, d3 * 0.4, "push_in", "50% 40%", transition_key="mask_reveal"),
        _Shot("shot-6", 3, scene3_start + d3 * 0.4, d3 * 0.6, "subtle_float", "50% 50%", transition_key="cut"),
    ]


def _build_shot_plan(request: LocalRenderRequest) -> list[_Shot]:
    if request.resolved_shots:
        return _shots_from_resolved(request.resolved_shots)
    return _legacy_shot_plan(request)


def _effective_text_cues(request: LocalRenderRequest, shots: list[_Shot]) -> list[TextCue]:
    if request.text_cues:
        return request.text_cues

    # Fallback: derive 3 cues timed to shots 1/4/6 (hook/message/CTA), preserving
    # the original shot-embedded-text behavior when no explicit cues are given.
    narration_by_scene = {scene.scene_number: scene.narration for scene in request.storyboard.scenes}
    headline, key_message, cta = _on_screen_text(request, narration_by_scene)
    hook_shot, message_shot, cta_shot = shots[0], shots[3], shots[5]
    return [
        TextCue(headline, hook_shot.start, hook_shot.duration),
        TextCue(key_message, message_shot.start, message_shot.duration),
        TextCue(cta, cta_shot.start, cta_shot.duration),
    ]


def _shot_html(shot: _Shot, image_filename: str) -> str:
    transition_html = ""
    if shot.transition_key == "mask_reveal":
        transition_html = (
            f'\n    <div class="transition-wipe" id="{shot.shot_id}-wipe" data-start="{shot.start:.3f}" '
            f'data-duration="0.6" data-track-index="3"></div>'
        )
    return (
        f'  <div class="shot" id="{shot.shot_id}" data-start="{shot.start:.3f}" '
        f'data-duration="{shot.duration:.3f}" data-track-index="1" data-camera="{shot.camera}">\n'
        f'    <div class="frame" style="background-image:url(\'{image_filename}\');'
        f'background-position:{shot.crop};"></div>'
        f"{transition_html}\n  </div>"
    )


def _text_cue_html(index: int, cue: TextCue) -> str:
    return (
        f'  <div class="text-card" id="text-cue-{index}" data-start="{cue.start_seconds:.3f}" '
        f'data-duration="{cue.duration_seconds:.3f}" data-track-index="2">{cue.text}</div>'
    )


def _text_cue_tween_js(index: int, cue: TextCue) -> str:
    fade_in_at = cue.start_seconds + cue.duration_seconds * 0.15
    fade_out_at = cue.start_seconds + cue.duration_seconds * 0.85
    return (
        f'  tl.fromTo("#text-cue-{index}", {{opacity: 0, y: 24}}, '
        f'{{opacity: 1, y: 0, duration: 0.5, ease: "power2.out"}}, {fade_in_at:.3f});\n'
        f'  tl.to("#text-cue-{index}", {{opacity: 0, duration: 0.4, ease: "power1.in"}}, {fade_out_at:.3f});'
    )


def _camera_tween_js(shot: _Shot) -> str:
    start_transform, end_transform = _CAMERA_TRANSFORMS[shot.camera]

    def _to_js_object(values: dict[str, float | str]) -> str:
        parts = [f'{key}: {value!r}' if isinstance(value, str) else f"{key}: {value}" for key, value in values.items()]
        return "{" + ", ".join(parts) + "}"

    return (
        f'  tl.fromTo("#{shot.shot_id} .frame", {_to_js_object(start_transform)}, '
        f'{{...{_to_js_object(end_transform)}, duration: {shot.duration:.3f}, ease: "sine.inOut"}}, {shot.start:.3f});'
    )


def _transition_tween_js(shot: _Shot) -> str:
    return (
        f'  tl.fromTo("#{shot.shot_id}-wipe", {{clipPath: "inset(0 0 0 100%)"}}, '
        f'{{clipPath: "inset(0 0 0 0%)", duration: 0.6, ease: "power2.inOut"}}, {shot.start:.3f});'
    )


def build_composition_html(
    request: LocalRenderRequest,
    *,
    image_filenames: dict[int, str],
    audio_filename: str,
) -> str:
    shots = _build_shot_plan(request)
    text_cues = _effective_text_cues(request, shots)
    total_duration = sum(request.scene_durations[n] for n in (1, 2, 3))

    shots_html = "\n".join(_shot_html(shot, image_filenames[shot.scene_number]) for shot in shots)
    text_cards_html = "\n".join(_text_cue_html(index, cue) for index, cue in enumerate(text_cues))
    camera_js = "\n".join(_camera_tween_js(shot) for shot in shots)
    text_js = "\n".join(_text_cue_tween_js(index, cue) for index, cue in enumerate(text_cues))
    transition_js = "\n".join(_transition_tween_js(shot) for shot in shots if shot.transition_key == "mask_reveal")

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{ margin: 0; padding: 0; background: #000; overflow: hidden; }}
  #stage {{ position: relative; width: {request.width}px; height: {request.height}px; overflow: hidden; }}
  .shot {{ position: absolute; inset: 0; opacity: 0; }}
  .frame {{
    position: absolute; inset: -6%; background-size: cover; background-repeat: no-repeat;
    transform-origin: center center; will-change: transform;
  }}
  .transition-wipe {{ position: absolute; inset: 0; background: #05070a; z-index: 2; }}
  .text-card {{
    position: absolute; left: 8%; right: 8%; bottom: 12%; z-index: 3; opacity: 0;
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-weight: 700;
    font-size: 64px; line-height: 1.15; color: #fdfdfd; text-shadow: 0 2px 24px rgba(0,0,0,0.55);
  }}
</style>
</head>
<body>
<div id="stage" data-composition-id="{_COMPOSITION_ID}" data-width="{request.width}" data-height="{request.height}"
     data-fps="{request.fps}">
{shots_html}
{text_cards_html}
  <audio data-start="0" data-duration="{total_duration:.3f}" data-track-index="0" src="{audio_filename}"></audio>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    tl.set(".shot", {{ opacity: 1 }}, 0);
{_shot_visibility_js(shots)}
{camera_js}
{text_js}
{transition_js}
    window.__timelines = window.__timelines || {{}};
    window.__timelines["{_COMPOSITION_ID}"] = tl;
  </script>
</div>
</body>
</html>
"""


def _shot_visibility_js(shots: list[_Shot]) -> str:
    lines = []
    for shot in shots:
        end = shot.start + shot.duration
        if shot.transition_key == "crossfade":
            lines.append(
                f'  tl.fromTo("#{shot.shot_id}", {{opacity: 0}}, '
                f'{{opacity: 1, duration: 0.5, ease: "power1.inOut"}}, {shot.start:.3f});'
            )
        else:
            lines.append(f'  tl.set("#{shot.shot_id}", {{opacity: 1}}, {shot.start:.3f});')
        lines.append(f'  tl.set("#{shot.shot_id}", {{opacity: 0}}, {end:.3f});')
    return "\n".join(lines)
