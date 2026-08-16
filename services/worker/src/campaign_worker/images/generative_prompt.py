import hashlib
from dataclasses import dataclass

from campaign_contracts.campaign import CampaignVersion, CreativeVideoPlan, StoryboardScene

from .creative_intent import asset_role_prompt_guidance, resolve_image_creative_intent

_NEGATIVE_SUFFIX = (
    "no text, no typography, no captions, no subtitles, no words, no letters, no numbers, "
    "no logos, no watermark, no signature, no UI elements"
)

# Applied to every plan-aware scene needing legible on-screen copy space (CTA
# frames, and any scene whose shots include a MESSAGE beat) -- see
# creative_intent.ImageCreativeIntent.text_safe_needed.
_TEXT_SAFE_GUIDANCE = (
    "Leave an uncluttered, text-safe negative space region suitable for on-screen copy; keep the "
    "subject positioned intentionally away from that area rather than centered across the whole frame."
)

# Applied to every plan-aware prompt: the generated image feeds a HyperFrames
# shot that will push in, pan, or reframe it, so the source image needs room
# to move within.
_VIDEO_AWARE_GUIDANCE = (
    "Compose for animation: leave margin around the subject, avoid cropping the product too tightly, "
    "preserve visual information near the frame edges, and create natural foreground/background depth "
    "so the shot supports push-in, pan, and reframing without revealing empty or repeated detail."
)

# Identical, constant text across all three scene prompts for a campaign --
# the shared phrasing itself is what nudges the model toward one coherent
# campaign look instead of three unrelated images (prompt-level consistency
# only; no image-to-image or external consistency provider).
_CONSISTENCY_GUIDANCE = (
    "Maintain a single consistent campaign visual language across every shot: matching lighting style, "
    "color mood, material and texture quality, and overall premium photographic finish."
)

_COMPOSITION_SUFFIX = (
    "Vertical 9:16 portrait composition, cinematic advertising lighting, high production value, photorealistic."
)


@dataclass(frozen=True, slots=True)
class GenerativePrompt:
    positive: str
    negative: str
    fingerprint: str


def _key_message(version: CampaignVersion) -> str:
    if version.strategy is not None and version.strategy.key_message:
        return version.strategy.key_message
    return version.brief.key_message or version.brief.campaign_goal


def _campaign_context_clauses(version: CampaignVersion, scene: StoryboardScene) -> list[str]:
    brief = version.brief
    return [
        f"Professional advertising photography for {brief.business_name}, "
        f"promoting {brief.product_or_service}. Tone: {brief.tone}. "
        f"Key message: {_key_message(version)}.",
        f"Scene {scene.scene_number} of 3 -- {scene.purpose}. Visual direction: {scene.visual_prompt}.",
    ]


def _build_legacy_prompt(version: CampaignVersion, scene: StoryboardScene) -> GenerativePrompt:
    positive = " ".join([*_campaign_context_clauses(version, scene), _COMPOSITION_SUFFIX])
    fingerprint = hashlib.sha256(f"{positive}\n{_NEGATIVE_SUFFIX}".encode()).hexdigest()
    return GenerativePrompt(positive=positive, negative=_NEGATIVE_SUFFIX, fingerprint=fingerprint)


def _build_creative_plan_prompt(
    version: CampaignVersion, scene: StoryboardScene, plan: CreativeVideoPlan
) -> GenerativePrompt:
    intents = resolve_image_creative_intent(plan)
    intent = intents.get(scene.scene_number)
    if intent is None:
        # No VideoShot references this scene (e.g. a hand-authored plan that
        # skips it) -- fall back safely rather than generating with no intent.
        return _build_legacy_prompt(version, scene)

    clauses = [
        *_campaign_context_clauses(version, scene),
        f"Campaign visual style: {plan.visual_style}.",
        asset_role_prompt_guidance(intent.primary_asset_role),
    ]
    if intent.visual_descriptions:
        clauses.append("Shot intent: " + " ".join(intent.visual_descriptions))
    if intent.text_safe_needed:
        clauses.append(_TEXT_SAFE_GUIDANCE)
    clauses.append(_VIDEO_AWARE_GUIDANCE)
    clauses.append(_CONSISTENCY_GUIDANCE)
    clauses.append(_COMPOSITION_SUFFIX)

    positive = " ".join(clauses)
    fingerprint = hashlib.sha256(f"{positive}\n{_NEGATIVE_SUFFIX}".encode()).hexdigest()
    return GenerativePrompt(positive=positive, negative=_NEGATIVE_SUFFIX, fingerprint=fingerprint)


def build_generative_prompt(version: CampaignVersion, scene: StoryboardScene) -> GenerativePrompt:
    if version.creative_video_plan is not None:
        return _build_creative_plan_prompt(version, scene, version.creative_video_plan)
    return _build_legacy_prompt(version, scene)
