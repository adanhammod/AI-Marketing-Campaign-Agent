"""CreativeVideoPlan generation: a deterministic generator, a Bedrock-backed
generator, and a fallback orchestrator that prefers the latter but always
falls back to the former on any failure.

Bedrock decides creative intent; everything downstream (image generation,
HyperFrames, audio cues) already consumes CreativeVideoPlan as-is (Slices 3-5)
regardless of which provider produced it -- this module is the only thing
that changes.
"""

import json
import logging
from typing import Any

from campaign_contracts.campaign import CampaignVersion, CreativeVideoPlan, VideoShot
from campaign_contracts.enums import AssetRole, AudioCueType, CameraMotion, ShotRole, TransitionType, VideoStyle
from pydantic import ValidationError

from campaign_worker.providers.base import CreativePlanProvider
from campaign_worker.providers.models import CreativePlanRequest, CreativePlanSceneContext
from campaign_worker.video.creative_plan_adapter import SUPPORTED_TRANSITIONS

_LOG = logging.getLogger(__name__)

# Bumped whenever the prompt or the expected output schema changes in a way
# that would make old logs/debugging context misleading. Not used for
# fingerprinting/idempotency -- tracked_step persistence already gives
# correct replay behavior (see graph/boundary.py::with_step_tracking).
CREATIVE_PLAN_PROMPT_VERSION = "v1"

MIN_SHOTS = 5
MAX_SHOTS = 8


def build_creative_plan_request(version: CampaignVersion) -> CreativePlanRequest:
    storyboard = version.storyboard
    strategy = version.strategy
    campaign_copy = version.campaign_copy
    assert storyboard is not None and strategy is not None and campaign_copy is not None
    brief = version.brief
    return CreativePlanRequest(
        business_name=brief.business_name,
        product_or_service=brief.product_or_service,
        campaign_goal=brief.campaign_goal,
        platforms=list(brief.platforms),
        target_audience=brief.target_audience,
        tone=brief.tone,
        video_style=brief.video_style,
        strategy_objective=strategy.objective,
        strategy_positioning=strategy.positioning,
        key_message=strategy.key_message,
        campaign_headline=campaign_copy.headline,
        call_to_action=campaign_copy.call_to_action,
        scenes=[
            CreativePlanSceneContext(
                scene_number=scene.scene_number,
                purpose=scene.purpose,
                visual_prompt=scene.visual_prompt,
                narration=scene.narration,
            )
            for scene in storyboard.scenes
        ],
        target_duration_seconds=version.constraints.target_duration_seconds,
    )


def _first_complete_sentence(text: str) -> str:
    # Mirrors graph/nodes.py::_first_complete_sentence -- kept as a local copy
    # (not imported) to avoid a circular import between nodes.py and this
    # module. Same narrative-safety convention: never cut mid-word/mid-clause.
    text = text.strip()
    for index, char in enumerate(text):
        if char in ".!?":
            return text[: index + 1]
    return f"{text}." if text else text


def _plan_shot(
    number: int,
    role: ShotRole,
    source_scene_number: int,
    asset_role: AssetRole,
    visual_description: str,
    duration_seconds: float,
    text: str | None,
    camera_motion: CameraMotion,
    transition_in: TransitionType,
    audio_cues: list[AudioCueType] | None = None,
) -> VideoShot:
    return VideoShot(
        shot_number=number,
        role=role,
        source_scene_number=source_scene_number,
        asset_role=asset_role,
        visual_description=visual_description,
        duration_seconds=duration_seconds,
        text=text,
        camera_motion=camera_motion,
        transition_in=transition_in,
        audio_cues=audio_cues or [],
    )


class DeterministicCreativePlanProvider(CreativePlanProvider):
    """The original template-based generator, preserved verbatim as the safe,
    proven fallback -- and as the default provider when Bedrock isn't
    configured at all."""

    async def generate(self, request: CreativePlanRequest) -> CreativeVideoPlan:
        scenes = {scene.scene_number: scene for scene in request.scenes}
        # 2 shots per source scene, same proportional splits HyperFrames' renderer
        # already proved (video/hyperframes_composition.py::_build_shot_plan).
        scene_duration = request.target_duration_seconds / 3

        hook_text = request.product_or_service[:200]
        message_text = _first_complete_sentence(request.key_message)[:200]
        cta_text = request.call_to_action[:200]

        shots = [
            _plan_shot(
                1,
                ShotRole.HOOK,
                1,
                AssetRole.HERO_PRODUCT,
                f"Attention-grabbing close crop on {scenes[1].visual_prompt}.",
                scene_duration * 0.5,
                hook_text,
                CameraMotion.PUSH_IN,
                TransitionType.CUT,
            ),
            _plan_shot(
                2,
                ShotRole.PRODUCT_HERO,
                1,
                AssetRole.HERO_PRODUCT,
                f"Wider premium product framing on {scenes[1].visual_prompt}.",
                scene_duration * 0.5,
                None,
                CameraMotion.PULL_OUT,
                TransitionType.CUT,
            ),
            _plan_shot(
                3,
                ShotRole.LIFESTYLE,
                2,
                AssetRole.LIFESTYLE_PRODUCT,
                f"Lifestyle context around {scenes[2].visual_prompt}.",
                scene_duration * 0.45,
                None,
                CameraMotion.PAN_LEFT,
                TransitionType.MASK_REVEAL,
                [AudioCueType.TRANSITION_HIT],
            ),
            _plan_shot(
                4,
                ShotRole.MESSAGE,
                2,
                AssetRole.LIFESTYLE_PRODUCT,
                "Stable framing with visual space reserved for the message copy.",
                scene_duration * 0.55,
                message_text,
                CameraMotion.STATIC,
                TransitionType.CUT,
            ),
            _plan_shot(
                5,
                ShotRole.PAYOFF,
                3,
                AssetRole.HERO_PRODUCT,
                f"Slow push-in product payoff on {scenes[3].visual_prompt}.",
                scene_duration * 0.4,
                None,
                CameraMotion.PUSH_IN,
                TransitionType.MASK_REVEAL,
                [AudioCueType.TRANSITION_HIT],
            ),
            _plan_shot(
                6,
                ShotRole.CTA,
                3,
                AssetRole.CTA_FRAME,
                "Clean end frame, held for readability.",
                scene_duration * 0.6,
                cta_text,
                CameraMotion.STATIC,
                TransitionType.CUT,
                [AudioCueType.BRAND_HIT],
            ),
        ]
        return CreativeVideoPlan(
            concept=request.campaign_headline,
            visual_style=f"{request.tone}, modern, premium",
            total_duration_seconds=request.target_duration_seconds,
            shots=shots,
        )


class CreativePlanValidationError(ValueError):
    """Raised when a generated response is well-formed JSON but violates the
    CreativeVideoPlan schema or a renderer/product-level semantic rule
    (unsupported transition, shot count out of range)."""


def _shot_role_values() -> str:
    return ", ".join(role.value for role in ShotRole)


def _asset_role_values() -> str:
    return ", ".join(role.value for role in AssetRole)


def _camera_motion_values() -> str:
    return ", ".join(motion.value for motion in CameraMotion)


def _supported_transition_values() -> str:
    return ", ".join(sorted(t.value for t in SUPPORTED_TRANSITIONS))


def _audio_cue_values() -> str:
    return ", ".join(cue.value for cue in AudioCueType)


def _video_style_guidance(video_style: VideoStyle) -> str:
    if video_style is VideoStyle.CINEMATIC_TEXT_AD:
        return (
            "This is a CINEMATIC_TEXT_AD: there is no voiceover. Text moments carry the message, so give "
            "most shots short on-screen text (1-6 words) at a music-first pace. Semantic audio_cues are "
            "encouraged (2-4 total) to accent transitions and the payoff."
        )
    if video_style is VideoStyle.VOICEOVER_AD:
        return (
            "This is a VOICEOVER_AD: narration will carry the message, so keep on-screen text minimal -- "
            "only a handful of shots should have any text at all (e.g. a product name or the CTA), never "
            "dense copy. Pacing must stay compatible with later proportional stretching to the real "
            "voiceover duration, so avoid extreme shot-duration outliers."
        )
    return "Keep on-screen text minimal and let the visuals carry the message."


def _build_prompt(request: CreativePlanRequest) -> str:
    scenes_context = [
        {
            "scene_number": scene.scene_number,
            "purpose": scene.purpose,
            "visual_prompt": scene.visual_prompt,
            "narration": scene.narration,
        }
        for scene in request.scenes
    ]
    campaign_context = {
        "business_name": request.business_name,
        "product_or_service": request.product_or_service,
        "campaign_goal": request.campaign_goal,
        "platforms": request.platforms,
        "target_audience": request.target_audience,
        "tone": request.tone,
        "video_style": request.video_style.value,
        "strategy_objective": request.strategy_objective,
        "strategy_positioning": request.strategy_positioning,
        "key_message": request.key_message,
        "campaign_headline": request.campaign_headline,
        "call_to_action": request.call_to_action,
        "storyboard_scenes": scenes_context,
        "target_duration_seconds": request.target_duration_seconds,
    }
    return (
        "You are a Creative Director / Social Video Editor producing a structured short-form ad edit plan "
        "-- not a marketing strategy document. The output is a shot-by-shot edit plan for a vertical 9:16 "
        f"advertising video, approximately {request.target_duration_seconds} seconds, in the style of "
        "Instagram/Reels/TikTok short-form advertising: a fast hook, premium visual rhythm, concise "
        "on-screen copy, and visually distinct shots.\n\n"
        f"{_video_style_guidance(request.video_style)}\n\n"
        "Campaign context (JSON):\n"
        f"{json.dumps(campaign_context, sort_keys=True)}\n\n"
        "Return ONLY a single JSON object matching exactly this schema -- no prose, no markdown code "
        "fences, no explanation before or after:\n"
        "{\n"
        '  "concept": string (1-200 chars, a one-line creative hook),\n'
        '  "visual_style": string (1-300 chars, short visual-direction phrase),\n'
        f'  "total_duration_seconds": integer, must equal {request.target_duration_seconds},\n'
        '  "shots": [\n'
        "    {\n"
        '      "shot_number": integer starting at 1, sequential with no gaps,\n'
        f'      "role": one of [{_shot_role_values()}],\n'
        '      "source_scene_number": integer, MUST be 1, 2, or 3 (there are only 3 source images -- '
        "multiple shots may deliberately reuse the same source_scene_number),\n"
        f'      "asset_role": one of [{_asset_role_values()}],\n'
        '      "visual_description": string (1-500 chars) describing FRAMING AND VISUAL INTENT ONLY -- '
        'e.g. "close product crop with condensation and strong foreground depth" or "clean hero frame '
        'with negative space on the left for CTA text". Never CSS, never GSAP, never pixel coordinates '
        "or transform/clip-path syntax,\n"
        '      "duration_seconds": number > 0,\n'
        '      "text": string (max 200 chars) or null -- when present, SHORT visual advertising copy '
        "only (1-6 words, a punchy phrase, product name, key benefit, or the CTA). Never a narration "
        "paragraph. Must stay faithful to the campaign's key message/product/CTA -- never fabricate "
        "unsupported product claims,\n"
        f'      "camera_motion": one of [{_camera_motion_values()}],\n'
        f'      "transition_in": one of [{_supported_transition_values()}] ONLY -- other transition '
        "values exist in the platform's enum but are not yet supported by the renderer and MUST NOT be "
        "used,\n"
        f'      "audio_cues": array, each one of [{_audio_cue_values()}] (usually empty; use 2-4 cues '
        "total across the whole plan, never on every shot)\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Use {MIN_SHOTS} to {MAX_SHOTS} shots total. The sum of every shot's duration_seconds must equal "
        f"total_duration_seconds ({request.target_duration_seconds}) within 0.01. Vary shot durations "
        "deliberately by role -- do not use equal-duration shots: a hook is short (~0.8-1.8s), hero/"
        "message beats are longer (~1.5-3s), detail/action beats are brief (~0.7-2s), and the CTA holds "
        "for readability (~1.5-3s). Avoid one long shot and avoid excessive micro-cuts (10+ shots).\n\n"
        "Every source_scene_number must be 1, 2, or 3. Multiple shots may reuse one source scene -- a "
        "common shape is scene 1 for hook+hero, scene 2 for lifestyle/action+message, scene 3 for "
        "detail/payoff+CTA, but do not force this exact template if the campaign calls for something "
        "else. Because image generation resolves ONE primary asset_role per source scene from every shot "
        "that references it, avoid assigning wildly contradictory asset_role values to the same "
        "source_scene_number unless creatively justified.\n\n"
        "Vary camera_motion across shots (e.g. mix PUSH_IN, STATIC, PAN_LEFT, MACRO_PUSH, PULL_OUT) -- "
        "do not repeat the same motion on every shot or move the camera for its own sake. For "
        "transition_in: CUT suits fast rhythm and detail beats, MASK_REVEAL suits larger scene changes "
        "or energetic transitions, CROSSFADE suits softer premium moments -- a good ad can still use CUT "
        "frequently, so do not overuse the other two."
    )


class BedrockCreativePlanProvider(CreativePlanProvider):
    def __init__(self, client: Any, model_id: str) -> None:
        self._client = client
        self._model_id = model_id

    async def generate(self, request: CreativePlanRequest) -> CreativeVideoPlan:
        payload = {
            "messages": [{"role": "user", "content": [{"text": _build_prompt(request)}]}],
            "inferenceConfig": {"maxTokens": 2000, "temperature": 0.6},
        }
        response = self._client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload).encode(),
        )
        try:
            parsed = json.loads(response["body"].read())
            text = parsed["output"]["message"]["content"][0]["text"]
            raw = json.loads(text)
            plan = CreativeVideoPlan.model_validate(raw)
            unsupported = [
                shot.shot_number for shot in plan.shots if shot.transition_in not in SUPPORTED_TRANSITIONS
            ]
            if unsupported:
                raise CreativePlanValidationError(
                    f"unsupported transition_in on shot(s) {unsupported}; only {sorted(t.value for t in SUPPORTED_TRANSITIONS)} are renderer-supported"
                )
            if not (MIN_SHOTS <= len(plan.shots) <= MAX_SHOTS):
                raise CreativePlanValidationError(
                    f"shot count {len(plan.shots)} outside allowed range [{MIN_SHOTS}, {MAX_SHOTS}]"
                )
        except (KeyError, TypeError, json.JSONDecodeError, ValidationError, CreativePlanValidationError) as exc:
            _LOG.warning(
                "creative_plan.provider_invalid",
                extra={"model": self._model_id, "reason": f"{type(exc).__name__}: {exc}"[:300]},
            )
            raise
        return plan


class FallbackCreativePlanProvider(CreativePlanProvider):
    """Tries `primary` first; on ANY exception, logs and falls back to
    `fallback`. CreativeVideoPlan generation is deliberately never a single
    point of failure -- the deterministic plan is valid and safe."""

    def __init__(self, primary: CreativePlanProvider, fallback: CreativePlanProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    async def generate(self, request: CreativePlanRequest) -> CreativeVideoPlan:
        _LOG.info(
            "creative_plan.provider_started",
            extra={"prompt_version": CREATIVE_PLAN_PROMPT_VERSION, "video_style": request.video_style.value},
        )
        try:
            plan = await self._primary.generate(request)
        except Exception as exc:
            _LOG.warning(
                "creative_plan.fallback_used",
                extra={"fallback_reason": f"{type(exc).__name__}: {exc}"[:300]},
            )
            return await self._fallback.generate(request)
        _LOG.info(
            "creative_plan.provider_succeeded",
            extra={"shot_count": len(plan.shots), "total_duration_seconds": plan.total_duration_seconds},
        )
        return plan
