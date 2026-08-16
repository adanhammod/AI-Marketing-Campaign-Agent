from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import (
    CampaignConstraints,
    CampaignVersion,
    CreativeVideoPlan,
    RetryMetadata,
    Storyboard,
    StoryboardScene,
    StrategyOutput,
    VideoShot,
)
from campaign_contracts.enums import AssetRole, CameraMotion, CampaignStatus, ShotRole, TransitionType

from campaign_worker.images.generative_prompt import build_generative_prompt


def _shot(
    number: int,
    scene: int,
    role: ShotRole,
    asset_role: AssetRole,
    *,
    visual_description: str = "shot description",
) -> VideoShot:
    return VideoShot(
        shot_number=number,
        role=role,
        source_scene_number=scene,
        asset_role=asset_role,
        visual_description=visual_description,
        duration_seconds=2.0,
        text=None,
        camera_motion=CameraMotion.STATIC,
        transition_in=TransitionType.CUT,
    )


def _plan(*, visual_style: str = "modern, energetic, premium") -> CreativeVideoPlan:
    shots = [
        _shot(1, 1, ShotRole.HOOK, AssetRole.HERO_PRODUCT, visual_description="Close crop hook"),
        _shot(2, 1, ShotRole.PRODUCT_HERO, AssetRole.HERO_PRODUCT, visual_description="Wide hero framing"),
        _shot(3, 2, ShotRole.LIFESTYLE, AssetRole.LIFESTYLE_PRODUCT, visual_description="Lifestyle context"),
        _shot(4, 2, ShotRole.MESSAGE, AssetRole.LIFESTYLE_PRODUCT, visual_description="Message framing"),
        _shot(5, 3, ShotRole.PAYOFF, AssetRole.HERO_PRODUCT, visual_description="Payoff push-in"),
        _shot(6, 3, ShotRole.CTA, AssetRole.CTA_FRAME, visual_description="Clean end frame"),
    ]
    return CreativeVideoPlan(
        concept="Test concept", visual_style=visual_style, total_duration_seconds=12, shots=shots
    )


def _version(*, strategy: StrategyOutput | None = None, key_message: str | None = None) -> CampaignVersion:
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals",
        key_message=key_message,
    )
    storyboard = Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration="Fresh coffee delivered",
                visual_prompt=f"artisan cold brew scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )
    return CampaignVersion(
        campaign_id=uuid4(),
        campaign_version=2,
        parent_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        strategy=strategy,
        storyboard=storyboard,
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )


def test_prompt_includes_business_product_tone_key_message_and_scene_visual_prompt():
    strategy = StrategyOutput(
        audience="Urban professionals",
        positioning="Premium local roaster",
        objective="Drive subscriptions",
        key_message="Fresh cold brew, delivered weekly",
        channel_rationale={"instagram": "visual-first audience"},
    )
    version = _version(strategy=strategy)
    scene = version.storyboard.scenes[1]

    prompt = build_generative_prompt(version, scene)

    assert "Example Coffee" in prompt.positive
    assert "Cold brew subscription" in prompt.positive
    assert "bright" in prompt.positive
    assert "Fresh cold brew, delivered weekly" in prompt.positive
    assert scene.purpose in prompt.positive
    assert scene.visual_prompt in prompt.positive
    assert "Scene 2 of 3" in prompt.positive
    assert "9:16" in prompt.positive


def test_prompt_falls_back_to_brief_key_message_then_campaign_goal_when_no_strategy():
    version = _version(strategy=None, key_message="Cold brew, always fresh")
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])
    assert "Cold brew, always fresh" in prompt.positive

    version_no_key_message = _version(strategy=None, key_message=None)
    prompt_no_key_message = build_generative_prompt(version_no_key_message, version_no_key_message.storyboard.scenes[0])
    assert "increase online subscription sales" in prompt_no_key_message.positive


def test_prompt_negative_instructions_exclude_text_typography_captions_watermarks_logos():
    version = _version()
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])

    for excluded in ("text", "typography", "caption", "watermark", "logo"):
        assert excluded in prompt.negative.lower()


def test_prompt_is_fully_derived_from_input_brief_with_no_hardcoded_campaign_literals():
    version = _version()
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])
    assert "luna" not in prompt.positive.lower()
    assert "coffee co" not in prompt.positive.lower()

    other = _version()
    other = other.model_copy(
        update={"brief": other.brief.model_copy(update={"business_name": "Totally Different Corp"})}
    )
    other_prompt = build_generative_prompt(other, other.storyboard.scenes[0])
    assert "Totally Different Corp" in other_prompt.positive
    assert "Example Coffee" not in other_prompt.positive


def test_identical_inputs_produce_identical_fingerprint_and_different_scenes_differ():
    version = _version()
    first = build_generative_prompt(version, version.storyboard.scenes[0])
    again = build_generative_prompt(version, version.storyboard.scenes[0])
    other_scene = build_generative_prompt(version, version.storyboard.scenes[1])

    assert first.fingerprint == again.fingerprint
    assert first.fingerprint != other_scene.fingerprint


# ---------------------------------------------------------------------------
# CreativeVideoPlan-aware prompt path
# ---------------------------------------------------------------------------


def _version_with_plan(**plan_overrides) -> CampaignVersion:
    version = _version()
    return version.model_copy(update={"creative_video_plan": _plan(**plan_overrides)})


def test_plan_aware_hero_product_scene_includes_premium_hero_guidance():
    version = _version_with_plan()
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])  # scene 1 -> HERO_PRODUCT
    assert "premium" in prompt.positive.lower()
    assert "hero" in prompt.positive.lower()


def test_plan_aware_lifestyle_scene_includes_lifestyle_guidance():
    version = _version_with_plan()
    prompt = build_generative_prompt(version, version.storyboard.scenes[1])  # scene 2 -> LIFESTYLE_PRODUCT
    assert "lifestyle" in prompt.positive.lower()


def test_plan_aware_cta_scene_includes_negative_space_guidance():
    version = _version_with_plan()
    prompt = build_generative_prompt(version, version.storyboard.scenes[2])  # scene 3 -> CTA_FRAME
    assert "negative space" in prompt.positive.lower() or "text-safe" in prompt.positive.lower()


def test_plan_aware_prompt_incorporates_visual_style():
    version = _version_with_plan(visual_style="retro, playful, bold")
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])
    assert "retro, playful, bold" in prompt.positive


def test_plan_aware_prompt_still_incorporates_storyboard_visual_prompt():
    version = _version_with_plan()
    scene = version.storyboard.scenes[0]
    prompt = build_generative_prompt(version, scene)
    assert scene.visual_prompt in prompt.positive


def test_plan_aware_prompt_still_incorporates_campaign_context():
    version = _version_with_plan()
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])
    assert version.brief.business_name in prompt.positive
    assert version.brief.product_or_service in prompt.positive


def test_plan_aware_prompt_never_asks_model_to_render_campaign_text():
    version = _version_with_plan()
    for scene in version.storyboard.scenes:
        prompt = build_generative_prompt(version, scene)
        lower = prompt.positive.lower()
        assert "headline" not in lower
        assert "call to action text" not in lower
        assert "render text" not in lower
    for excluded in ("text", "typography", "caption", "watermark", "logo"):
        assert excluded in prompt.negative.lower()


def test_plan_aware_video_aware_framing_guidance_present():
    version = _version_with_plan()
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])
    lower = prompt.positive.lower()
    assert "margin" in lower or "crop" in lower or "depth" in lower


def test_plan_aware_all_three_scenes_share_consistent_campaign_visual_language_clause():
    version = _version_with_plan()
    prompts = [build_generative_prompt(version, scene) for scene in version.storyboard.scenes]
    # A shared, identical consistency clause across all three prompts drives
    # the model toward one coherent campaign look, not three disconnected shots.
    assert "consistent campaign visual language" in prompts[0].positive.lower()
    assert "consistent campaign visual language" in prompts[1].positive.lower()
    assert "consistent campaign visual language" in prompts[2].positive.lower()


def test_no_plan_campaign_uses_legacy_prompt_behavior():
    version = _version()  # no creative_video_plan
    assert version.creative_video_plan is None
    prompt = build_generative_prompt(version, version.storyboard.scenes[0])
    # Legacy prompt never mentions AssetRole-specific guidance vocabulary.
    assert "text-safe" not in prompt.positive.lower()
    assert "negative space" not in prompt.positive.lower()


def test_plan_aware_fingerprint_stable_for_identical_inputs():
    version = _version_with_plan()
    scene = version.storyboard.scenes[0]
    first = build_generative_prompt(version, scene)
    second = build_generative_prompt(version, scene)
    assert first.fingerprint == second.fingerprint


def test_plan_aware_fingerprint_changes_when_visual_style_changes():
    version_a = _version_with_plan(visual_style="modern, energetic, premium")
    version_b = _version_with_plan(visual_style="retro, playful, bold")
    scene = version_a.storyboard.scenes[0]
    prompt_a = build_generative_prompt(version_a, scene)
    prompt_b = build_generative_prompt(version_b, scene)
    assert prompt_a.fingerprint != prompt_b.fingerprint


def test_plan_aware_fingerprint_changes_when_scenes_primary_asset_role_changes():
    version = _version_with_plan()
    scene = version.storyboard.scenes[2]  # CTA_FRAME scene
    with_cta = build_generative_prompt(version, scene)

    # Rebuild the plan so scene 3 only carries a HERO_PRODUCT shot (no CTA_FRAME).
    shots = [
        _shot(1, 1, ShotRole.HOOK, AssetRole.HERO_PRODUCT),
        _shot(2, 2, ShotRole.LIFESTYLE, AssetRole.LIFESTYLE_PRODUCT),
        _shot(3, 3, ShotRole.PAYOFF, AssetRole.HERO_PRODUCT),
    ]
    hero_only_plan = CreativeVideoPlan(
        concept="Test concept", visual_style="modern, energetic, premium", total_duration_seconds=6, shots=shots
    )
    version_hero_only = version.model_copy(update={"creative_video_plan": hero_only_plan})
    without_cta = build_generative_prompt(version_hero_only, version_hero_only.storyboard.scenes[2])

    assert with_cta.fingerprint != without_cta.fingerprint


def test_plan_aware_fingerprint_differs_from_legacy_no_plan_fingerprint():
    version_no_plan = _version()
    version_with_plan = _version_with_plan()
    scene = version_no_plan.storyboard.scenes[0]
    legacy = build_generative_prompt(version_no_plan, scene)
    plan_aware = build_generative_prompt(version_with_plan, version_with_plan.storyboard.scenes[0])
    assert legacy.fingerprint != plan_aware.fingerprint
