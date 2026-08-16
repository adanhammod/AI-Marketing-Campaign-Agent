"""Resolves per-scene image-generation creative intent from a CreativeVideoPlan.

A single storyboard scene's image may be referenced by multiple VideoShots
carrying different AssetRoles (e.g. scene 3 might serve both a HERO_PRODUCT
payoff shot and a CTA_FRAME end shot). This module picks one deterministic
primary intent per scene and provides the AssetRole-specific prompt guidance
the image generator uses to express it -- keeping "what image to generate and
why" separate from "how to phrase the Stability prompt" (generative_prompt.py).
"""

from dataclasses import dataclass

from campaign_contracts.campaign import CreativeVideoPlan, VideoShot
from campaign_contracts.enums import AssetRole, ShotRole


@dataclass(frozen=True, slots=True)
class ImageCreativeIntent:
    scene_number: int
    primary_asset_role: AssetRole
    visual_descriptions: tuple[str, ...]
    text_safe_needed: bool


# Resolution priority when a scene's shots carry more than one AssetRole.
# CTA_FRAME is ranked first, above every content-style role, because whichever
# shot ultimately overlays on-screen CTA text on this image is a *hard*
# downstream legibility constraint -- not a style preference like the rest.
# Among the remaining, content-style roles: ACTION_SHOT is the most visually
# distinctive/highest-energy role (useful as a scene's defining beat), then
# HERO_PRODUCT (the clear premium-product default), then LIFESTYLE_PRODUCT,
# then DETAIL_SHOT (narrowest in scope -- a macro/texture beat rarely makes an
# adequate primary image for an entire scene).
_ASSET_ROLE_PRIORITY: tuple[AssetRole, ...] = (
    AssetRole.CTA_FRAME,
    AssetRole.ACTION_SHOT,
    AssetRole.HERO_PRODUCT,
    AssetRole.LIFESTYLE_PRODUCT,
    AssetRole.DETAIL_SHOT,
)

_ASSET_ROLE_GUIDANCE: dict[AssetRole, str] = {
    AssetRole.HERO_PRODUCT: (
        "Premium hero product shot: cinematic commercial lighting, luxury advertising photography. "
        "The product is unmistakably the hero of the frame, with strong subject separation from the "
        "background, premium studio shadows and highlights, and a clean, ad-ready composition. Render "
        "the product large enough to read clearly on a mobile screen. Avoid generic centered-object "
        "catalog photography."
    ),
    AssetRole.ACTION_SHOT: (
        "Dynamic action shot: frozen-action commercial photography with strong, controlled energy -- a "
        "category-appropriate dynamic moment such as pouring, splashing, steam, particles, or motion, "
        "whichever suits the product -- while keeping the product clearly identifiable. Visually dramatic "
        "but composed, suitable for a hook or an energetic transition."
    ),
    AssetRole.DETAIL_SHOT: (
        "Macro detail shot: close-up on the product's texture, material, or packaging detail, shallow "
        "depth of field, premium lighting. Visually distinct from a hero shot -- a short detail beat, "
        "not a wide product view."
    ),
    AssetRole.LIFESTYLE_PRODUCT: (
        "Lifestyle product shot: a believable, real-world premium use context in a modern setting. The "
        "product remains visually central and clearly the subject; if a person appears, they support the "
        "product rather than dominate the frame. Clean, intentional background -- avoid generic "
        "stock-photo composition."
    ),
    AssetRole.CTA_FRAME: (
        "Clean CTA-ready frame: strong product visibility combined with intentional negative space, an "
        "uncluttered text-safe region, low clutter, and a clear visual hierarchy. Asymmetrical composition "
        "with the subject positioned away from the likely copy area -- suitable for a call-to-action or "
        "end-card overlay."
    ),
}


def asset_role_prompt_guidance(role: AssetRole) -> str:
    return _ASSET_ROLE_GUIDANCE[role]


def _resolve_primary_asset_role(shots: list[VideoShot]) -> AssetRole:
    present = {shot.asset_role for shot in shots}
    for candidate in _ASSET_ROLE_PRIORITY:
        if candidate in present:
            return candidate
    # Unreachable: AssetRole is a closed enum and _ASSET_ROLE_PRIORITY covers
    # every member, so `present` (non-empty, since shots is non-empty) always
    # intersects it.
    raise ValueError(f"no supported asset role found among {present}")


def resolve_image_creative_intent(plan: CreativeVideoPlan) -> dict[int, ImageCreativeIntent]:
    shots_by_scene: dict[int, list[VideoShot]] = {}
    for shot in plan.shots:
        shots_by_scene.setdefault(shot.source_scene_number, []).append(shot)

    intents: dict[int, ImageCreativeIntent] = {}
    for scene_number, shots in shots_by_scene.items():
        descriptions = tuple(dict.fromkeys(shot.visual_description for shot in shots))
        text_safe_needed = any(
            shot.asset_role == AssetRole.CTA_FRAME or shot.role == ShotRole.MESSAGE for shot in shots
        )
        intents[scene_number] = ImageCreativeIntent(
            scene_number=scene_number,
            primary_asset_role=_resolve_primary_asset_role(shots),
            visual_descriptions=descriptions,
            text_safe_needed=text_safe_needed,
        )
    return intents
