import pytest
from campaign_contracts.campaign import CreativeVideoPlan, VideoShot
from campaign_contracts.enums import AssetRole, CameraMotion, ShotRole, TransitionType

from campaign_worker.images.creative_intent import (
    ImageCreativeIntent,
    asset_role_prompt_guidance,
    resolve_image_creative_intent,
)


def _shot(
    number: int,
    scene: int,
    role: ShotRole,
    asset_role: AssetRole,
    *,
    visual_description: str = "shot description",
    text: str | None = None,
) -> VideoShot:
    return VideoShot(
        shot_number=number,
        role=role,
        source_scene_number=scene,
        asset_role=asset_role,
        visual_description=visual_description,
        duration_seconds=2.0,
        text=text,
        camera_motion=CameraMotion.STATIC,
        transition_in=TransitionType.CUT,
    )


def _plan(shots: list[VideoShot]) -> CreativeVideoPlan:
    return CreativeVideoPlan(
        concept="Test concept",
        visual_style="modern, energetic, premium",
        total_duration_seconds=round(sum(s.duration_seconds for s in shots)),
        shots=shots,
    )


def _deterministic_plan() -> CreativeVideoPlan:
    # Mirrors graph/nodes.py::create_creative_plan's real deterministic template.
    return _plan(
        [
            _shot(1, 1, ShotRole.HOOK, AssetRole.HERO_PRODUCT, visual_description="Close crop hook"),
            _shot(2, 1, ShotRole.PRODUCT_HERO, AssetRole.HERO_PRODUCT, visual_description="Wide hero framing"),
            _shot(3, 2, ShotRole.LIFESTYLE, AssetRole.LIFESTYLE_PRODUCT, visual_description="Lifestyle context"),
            _shot(4, 2, ShotRole.MESSAGE, AssetRole.LIFESTYLE_PRODUCT, visual_description="Message framing", text="msg"),
            _shot(5, 3, ShotRole.PAYOFF, AssetRole.HERO_PRODUCT, visual_description="Payoff push-in"),
            _shot(6, 3, ShotRole.CTA, AssetRole.CTA_FRAME, visual_description="Clean end frame", text="cta"),
        ]
    )


# ---------------------------------------------------------------------------
# Grouping + deterministic single primary intent per scene
# ---------------------------------------------------------------------------


def test_shots_are_grouped_by_source_scene_number():
    intents = resolve_image_creative_intent(_deterministic_plan())
    assert set(intents.keys()) == {1, 2, 3}


def test_resolution_is_deterministic_across_calls():
    plan = _deterministic_plan()
    first = resolve_image_creative_intent(plan)
    second = resolve_image_creative_intent(plan)
    assert first == second


def test_scene_with_only_hero_product_shots_resolves_hero_product():
    intents = resolve_image_creative_intent(_deterministic_plan())
    assert intents[1].primary_asset_role == AssetRole.HERO_PRODUCT


def test_scene_with_only_lifestyle_shots_resolves_lifestyle_product():
    intents = resolve_image_creative_intent(_deterministic_plan())
    assert intents[2].primary_asset_role == AssetRole.LIFESTYLE_PRODUCT


def test_scene_mixing_hero_product_and_cta_frame_resolves_cta_frame():
    # Scene 3 in the real deterministic plan mixes PAYOFF/HERO_PRODUCT and
    # CTA/CTA_FRAME. CTA_FRAME must win: whichever shot carries the on-screen
    # CTA text is a hard downstream legibility constraint on this image's
    # composition, not merely a content-style preference like the other roles.
    intents = resolve_image_creative_intent(_deterministic_plan())
    assert intents[3].primary_asset_role == AssetRole.CTA_FRAME


def test_action_shot_outranks_hero_product_and_lifestyle_when_no_cta_present():
    plan = _plan(
        [
            _shot(1, 1, ShotRole.HOOK, AssetRole.HERO_PRODUCT),
            _shot(2, 1, ShotRole.ACTION, AssetRole.ACTION_SHOT),
            _shot(3, 1, ShotRole.LIFESTYLE, AssetRole.LIFESTYLE_PRODUCT),
        ]
    )
    intents = resolve_image_creative_intent(plan)
    assert intents[1].primary_asset_role == AssetRole.ACTION_SHOT


def test_detail_shot_is_lowest_priority_content_style_role():
    plan = _plan(
        [
            _shot(1, 1, ShotRole.DETAIL, AssetRole.DETAIL_SHOT),
            _shot(2, 1, ShotRole.LIFESTYLE, AssetRole.LIFESTYLE_PRODUCT),
        ]
    )
    intents = resolve_image_creative_intent(plan)
    assert intents[1].primary_asset_role == AssetRole.LIFESTYLE_PRODUCT


def test_single_shot_scene_resolves_that_shots_own_role():
    plan = _plan([_shot(1, 1, ShotRole.DETAIL, AssetRole.DETAIL_SHOT)])
    intents = resolve_image_creative_intent(plan)
    assert intents[1].primary_asset_role == AssetRole.DETAIL_SHOT


# ---------------------------------------------------------------------------
# text_safe_needed
# ---------------------------------------------------------------------------


def test_text_safe_needed_true_when_cta_frame_present():
    intents = resolve_image_creative_intent(_deterministic_plan())
    assert intents[3].text_safe_needed is True


def test_text_safe_needed_true_when_message_role_present_even_without_cta_frame():
    plan = _plan(
        [
            _shot(1, 1, ShotRole.MESSAGE, AssetRole.LIFESTYLE_PRODUCT),
        ]
    )
    intents = resolve_image_creative_intent(plan)
    assert intents[1].text_safe_needed is True


def test_text_safe_needed_false_for_scene_with_only_hero_product_hook():
    intents = resolve_image_creative_intent(_deterministic_plan())
    assert intents[1].text_safe_needed is False


# ---------------------------------------------------------------------------
# visual_descriptions
# ---------------------------------------------------------------------------


def test_visual_descriptions_preserve_order_and_dedupe():
    plan = _plan(
        [
            _shot(1, 1, ShotRole.HOOK, AssetRole.HERO_PRODUCT, visual_description="Close crop"),
            _shot(2, 1, ShotRole.PRODUCT_HERO, AssetRole.HERO_PRODUCT, visual_description="Wide framing"),
            _shot(3, 1, ShotRole.PAYOFF, AssetRole.HERO_PRODUCT, visual_description="Close crop"),
        ]
    )
    intents = resolve_image_creative_intent(plan)
    assert intents[1].visual_descriptions == ("Close crop", "Wide framing")


# ---------------------------------------------------------------------------
# AssetRole -> prompt guidance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(AssetRole))
def test_every_asset_role_has_explicit_prompt_guidance(role):
    guidance = asset_role_prompt_guidance(role)
    assert isinstance(guidance, str) and guidance.strip()


def test_hero_product_guidance_contains_premium_product_ad_intent():
    guidance = asset_role_prompt_guidance(AssetRole.HERO_PRODUCT).lower()
    assert "premium" in guidance
    assert "hero" in guidance
    assert "product" in guidance


def test_action_shot_guidance_contains_dynamic_action_intent():
    guidance = asset_role_prompt_guidance(AssetRole.ACTION_SHOT).lower()
    assert "motion" in guidance or "dynamic" in guidance or "action" in guidance


def test_detail_shot_guidance_contains_macro_detail_intent():
    guidance = asset_role_prompt_guidance(AssetRole.DETAIL_SHOT).lower()
    assert "macro" in guidance or "close-up" in guidance
    assert "detail" in guidance or "texture" in guidance


def test_lifestyle_product_guidance_keeps_product_central_and_avoids_generic_stock():
    guidance = asset_role_prompt_guidance(AssetRole.LIFESTYLE_PRODUCT).lower()
    assert "central" in guidance or "remains" in guidance
    assert "generic" in guidance and "stock" in guidance


def test_cta_frame_guidance_requests_negative_text_safe_space():
    guidance = asset_role_prompt_guidance(AssetRole.CTA_FRAME).lower()
    assert "negative space" in guidance or "text-safe" in guidance


def test_asset_role_guidance_never_asks_model_to_render_campaign_text():
    for role in AssetRole:
        guidance = asset_role_prompt_guidance(role).lower()
        assert "headline" not in guidance
        assert "render text" not in guidance
        assert "write" not in guidance


def test_image_creative_intent_is_a_plain_frozen_dataclass_instance():
    intents = resolve_image_creative_intent(_deterministic_plan())
    assert isinstance(intents[1], ImageCreativeIntent)
