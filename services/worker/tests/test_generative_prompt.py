from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import (
    CampaignConstraints,
    CampaignVersion,
    RetryMetadata,
    Storyboard,
    StoryboardScene,
    StrategyOutput,
)
from campaign_contracts.enums import CampaignStatus

from campaign_worker.images.generative_prompt import build_generative_prompt


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
