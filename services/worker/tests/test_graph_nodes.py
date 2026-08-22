import ast
import inspect
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, StrategyOutput
from campaign_contracts.enums import CampaignStatus, ErrorComponent, StepStatus, VideoStyle, WorkflowStep
from campaign_contracts.errors import SanitizedWorkflowError
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.audio import narration_timing
from campaign_worker.graph import nodes
from campaign_worker.graph.boundary import NodeCancelled, with_step_tracking
from campaign_worker.graph.creative_plan_provider import DeterministicCreativePlanProvider
from campaign_worker.graph.state import GraphState
from campaign_worker.providers.base import ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.providers.mock_image_provider import MockImageProvider
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.providers.models import (
    ImageGenerationRequest,
    ImageGenerationResult,
    VideoRenderRequest,
    VideoRenderResult,
)
from campaign_worker.providers.voice_models import VoiceGenerationRequest, VoiceGenerationResult
from campaign_worker.repositories.workflow_repository import WorkflowRepository


def _version(**overrides):
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram", "facebook"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals aged 25-40",
        call_to_action="Subscribe today",
    )
    defaults = dict(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


@pytest.mark.asyncio
async def test_receive_request_and_analyze_campaign_are_passthrough_safe():
    state: GraphState = {"version": _version()}
    after_receive = await nodes.receive_request(state)
    after_analyze = await nodes.analyze_campaign(after_receive)
    assert after_analyze["version"].campaign_id == state["version"].campaign_id


@pytest.mark.asyncio
async def test_validate_input_rejects_blank_description():
    brief = _version().brief.model_copy(update={"business_description": "                    "})
    state: GraphState = {"version": _version(brief=brief)}
    with pytest.raises(ValueError, match="business_description"):
        await nodes.validate_input(state)


@pytest.mark.asyncio
async def test_validate_input_accepts_populated_description():
    state: GraphState = {"version": _version()}
    result = await nodes.validate_input(state)
    assert result["version"].campaign_id == state["version"].campaign_id


@pytest.mark.asyncio
async def test_create_strategy_produces_schema_valid_output():
    state: GraphState = {"version": _version()}
    result = await nodes.create_strategy(state)
    strategy = result["version"].strategy
    assert strategy is not None
    assert strategy.audience == "Urban professionals aged 25-40"
    assert "instagram" in strategy.channel_rationale
    assert "facebook" in strategy.channel_rationale


@pytest.mark.asyncio
async def test_create_strategy_falls_back_to_general_audience_when_unspecified():
    version = _version()
    brief = version.brief.model_copy(update={"target_audience": None})
    state: GraphState = {"version": version.model_copy(update={"brief": brief})}
    result = await nodes.create_strategy(state)
    assert result["version"].strategy.audience == "general audience"


@pytest.mark.asyncio
async def test_generate_copy_requires_prior_strategy():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="create_strategy"):
        await nodes.generate_copy(state)


@pytest.mark.asyncio
async def test_generate_copy_produces_schema_valid_output():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    result = await nodes.generate_copy(strategized)
    copy = result["version"].campaign_copy
    assert copy is not None
    assert copy.call_to_action == "Subscribe today"
    assert len(copy.channel_variants) == 2
    assert {variant.channel for variant in copy.channel_variants} == {"instagram", "facebook"}


@pytest.mark.asyncio
async def test_create_storyboard_requires_prior_strategy():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="create_strategy"):
        await nodes.create_storyboard(state)


@pytest.mark.asyncio
async def test_create_storyboard_produces_three_scenes_totaling_valid_duration():
    state: GraphState = {"version": _version()}
    strategized = await nodes.create_strategy(state)
    copied = await nodes.generate_copy(strategized)
    result = await nodes.create_storyboard(copied)
    storyboard = result["version"].storyboard
    assert storyboard is not None
    assert [s.scene_number for s in storyboard.scenes] == [1, 2, 3]
    assert storyboard.total_duration_seconds == sum(s.duration_seconds for s in storyboard.scenes)
    assert 13 <= storyboard.total_duration_seconds <= 17


def _short_message_brief(**overrides):
    defaults = dict(
        business_name="Luna Coffee",
        product_or_service="Luna Cold Brew",
        business_description="Luna Coffee offers small-batch cold brew roasted weekly for local cafes.",
        campaign_goal="Promote our new premium coffee collection and increase online sales.",
        platforms=["instagram", "tiktok"],
        tone="Warm, modern, premium",
        language="en-US",
        target_audience="Young professionals and coffee lovers aged 22-40.",
        key_message="Premium coffee that turns your everyday morning into a special moment.",
        call_to_action="Discover the collection.",
    )
    defaults.update(overrides)
    return CampaignCreationRequest(**defaults)


@pytest.mark.asyncio
async def test_create_storyboard_generates_distinct_narration_for_short_key_message():
    # Regression test for the bug where all three scenes got the exact same
    # short strategy.key_message as narration, which produced Polly audio
    # too short to satisfy the video pipeline's duration-scale validation.
    brief = _short_message_brief()
    state: GraphState = {"version": _version(brief=brief)}
    strategized = await nodes.create_strategy(state)
    result = await nodes.create_storyboard(strategized)
    storyboard = result["version"].storyboard
    assert storyboard is not None

    narrations = [scene.narration for scene in storyboard.scenes]

    # No longer three identical copies of key_message.
    assert len(set(narrations)) == 3
    for narration in narrations:
        assert narration != brief.key_message

    # Combined narration should be meaningfully longer than the old
    # 3x-key_message-repeat baseline, giving a real TTS engine enough
    # content to plausibly land near the 15s storyboard target.
    combined_word_count = sum(len(n.split()) for n in narrations)
    baseline_word_count = len((brief.key_message or "").split()) * 3
    assert combined_word_count > baseline_word_count

    # Each scene's narration should reference real brief/strategy content,
    # not be boilerplate/empty.
    assert brief.business_name in narrations[0]
    assert brief.product_or_service in narrations[0]
    assert brief.key_message in narrations[1]
    assert (brief.call_to_action or "Learn more") in narrations[2]


@pytest.mark.asyncio
async def test_create_storyboard_narration_uses_fallbacks_when_optional_brief_fields_absent():
    brief = _short_message_brief(key_message=None, call_to_action=None, target_audience=None)
    state: GraphState = {"version": _version(brief=brief)}
    strategized = await nodes.create_strategy(state)
    result = await nodes.create_storyboard(strategized)
    narrations = [scene.narration for scene in result["version"].storyboard.scenes]

    assert len(set(narrations)) == 3
    assert "Learn more" in narrations[2]
    # Falls back to campaign_goal when key_message is absent. Note:
    # target_audience is intentionally never narrated (see _scene_narration),
    # so there is no "general audience" fallback to assert here anymore.
    assert brief.campaign_goal in narrations[1]


@pytest.mark.asyncio
async def test_create_storyboard_narration_is_more_conservative_than_previous_luna_incident():
    # Regression guard for a real production incident: the first iteration
    # of this narration fix (commit e72bdd5) produced 44 words combined for
    # this exact brief, which Polly measured at 18.696s. (18.696s is itself
    # valid under the current 13-20s hard bound and would no longer fail --
    # see test_c8_video.py's real-incident regression test -- but 44 words
    # is still well above the preferred TARGET band this function aims for.)
    # This does NOT assert any specific duration (Polly timing is not a
    # deterministic function of word count alone -- see the two real,
    # non-linear data points from this incident); it only guards that the
    # retargeted narration is shorter than that known baseline for the same
    # brief content.
    brief = _short_message_brief()
    state: GraphState = {"version": _version(brief=brief)}
    strategized = await nodes.create_strategy(state)
    result = await nodes.create_storyboard(strategized)
    narrations = [scene.narration for scene in result["version"].storyboard.scenes]

    combined_word_count = sum(len(n.split()) for n in narrations)
    previous_luna_incident_word_count = 44
    assert combined_word_count < previous_luna_incident_word_count


@pytest.mark.asyncio
async def test_create_storyboard_narration_never_narrates_raw_target_audience_verbatim():
    # Regression guard for the real Luna incident: narration must never
    # splice a raw demographic/targeting-spec string (e.g. "aged 22-40")
    # into spoken ad copy, for any brief -- not just Luna's specific wording.
    brief = _short_message_brief()
    state: GraphState = {"version": _version(brief=brief)}
    strategized = await nodes.create_strategy(state)
    result = await nodes.create_storyboard(strategized)
    narrations = [scene.narration for scene in result["version"].storyboard.scenes]
    assert brief.target_audience is not None
    for narration in narrations:
        assert brief.target_audience not in narration


def test_scene_narration_is_deterministic():
    brief = _short_message_brief()
    strategy = StrategyOutput(
        audience="Young professionals and coffee lovers aged 22-40.",
        positioning="Luna Coffee for Luna Cold Brew",
        objective=brief.campaign_goal,
        key_message=brief.key_message or brief.campaign_goal,
        channel_rationale={},
    )
    first = [nodes._scene_narration(n, brief, strategy) for n in (1, 2, 3)]
    second = [nodes._scene_narration(n, brief, strategy) for n in (1, 2, 3)]
    assert first == second
    assert len(set(first)) == 3


def _very_short_brief(**overrides):
    # Short but real content -- not the absolute schema-legal minimum
    # (2-char fields), which is a documented residual edge case that this
    # word-count mechanism alone cannot always guarantee reaches the target
    # band without fabricating unnatural filler (see narration_timing.py).
    defaults = dict(
        business_name="Zola Tea",
        product_or_service="Herbal Tea Blends",
        business_description="A cozy neighborhood tea shop with hand-blended herbal teas.",
        campaign_goal="Grow local tea sales.",
        platforms=["instagram"],
        tone="Warm and cozy",
        language="en-US",
        target_audience=None,
        key_message=None,
        call_to_action=None,
    )
    defaults.update(overrides)
    return CampaignCreationRequest(**defaults)


def _very_long_brief(**overrides):
    long_business = ("Luna Coffee Roasters " * 10)[:120].strip()
    long_product = ("Specialty Cold Brew Subscription Box " * 10)[:200].strip()
    # Realistic long fields are multi-sentence prose, not one giant run-on --
    # each repetition ends in a period so _first_complete_sentence has a
    # real, early sentence boundary to find (proving it drops the rest
    # rather than cutting mid-sentence/mid-word).
    long_key_message = ("Smooth energy for your day. " * 10)[:500].strip()
    long_cta = ("Discover the whole collection today. " * 10)[:200].strip()
    defaults = dict(
        business_name=long_business,
        product_or_service=long_product,
        business_description="A local roaster offering weekly cold brew delivery to nearby cafes and offices.",
        campaign_goal="Increase online subscription sales for our new premium cold brew lineup.",
        platforms=["instagram"],
        tone="Bold, modern, and energetic",
        language="en-US",
        target_audience="Young professionals",
        key_message=long_key_message,
        call_to_action=long_cta,
    )
    defaults.update(overrides)
    return CampaignCreationRequest(**defaults)


async def _storyboard_narrations(brief) -> list[str]:
    state: GraphState = {"version": _version(brief=brief)}
    strategized = await nodes.create_strategy(state)
    result = await nodes.create_storyboard(strategized)
    return [scene.narration for scene in result["version"].storyboard.scenes]


@pytest.mark.asyncio
async def test_create_storyboard_narration_lands_in_preferred_band_for_a_normal_brief():
    narrations = await _storyboard_narrations(_short_message_brief())
    total = sum(narration_timing.word_count(n) for n in narrations)
    min_words, max_words = narration_timing.target_word_range()
    assert min_words <= total <= max_words
    estimated = narration_timing.estimate_seconds(total)
    assert narration_timing.TARGET_MIN_SECONDS <= estimated <= narration_timing.TARGET_MAX_SECONDS


@pytest.mark.asyncio
async def test_create_storyboard_narration_total_word_count_stays_within_designed_range():
    # A second, distinct brief (not Luna-themed) proves the band isn't an
    # artifact of one specific fixture's word lengths.
    narrations = await _storyboard_narrations(_version().brief)
    total = sum(narration_timing.word_count(n) for n in narrations)
    min_words, max_words = narration_timing.target_word_range()
    assert min_words <= total <= max_words


@pytest.mark.asyncio
async def test_create_storyboard_narration_extends_short_briefs_toward_the_target_band():
    narrations = await _storyboard_narrations(_very_short_brief())
    total = sum(narration_timing.word_count(n) for n in narrations)
    min_words, max_words = narration_timing.target_word_range()
    assert min_words <= total <= max_words
    # The extension content is genuine campaign content (business_description
    # and/or tone), not a repeated fixed phrase.
    assert len(set(narrations)) == 3


@pytest.mark.asyncio
async def test_create_storyboard_narration_extension_never_narrates_target_audience():
    # Same production-incident guard as the core templates: even when
    # extending short narration, target_audience must never be spliced in.
    brief = _very_short_brief(target_audience="Busy parents aged 30-45")
    narrations = await _storyboard_narrations(brief)
    for narration in narrations:
        assert "Busy parents aged 30-45" not in narration


@pytest.mark.asyncio
async def test_create_storyboard_narration_never_cuts_long_key_message_or_cta_mid_sentence():
    # For long, multi-sentence fields, only the first complete sentence is
    # used -- the rest is dropped whole, never cut mid-sentence/mid-word.
    brief = _very_long_brief()
    narrations = await _storyboard_narrations(brief)
    expected_key_message = nodes._first_complete_sentence(brief.key_message)
    expected_cta = nodes._first_complete_sentence(brief.call_to_action)
    assert expected_key_message in narrations[1]
    assert expected_cta in narrations[2]
    # The dropped remainder must not appear anywhere (proves it was cleanly
    # excluded, not truncated into a dangling fragment).
    remainder = brief.key_message[len(expected_key_message) :].strip()
    assert remainder and remainder not in narrations[1]


@pytest.mark.asyncio
async def test_create_storyboard_narration_never_truncates_long_product_or_business_names():
    brief = _very_long_brief()
    narrations = await _storyboard_narrations(brief)
    combined = " ".join(narrations)
    assert brief.business_name in combined
    assert brief.product_or_service in combined


@pytest.mark.asyncio
async def test_create_storyboard_narration_scenes_remain_non_empty_and_well_formed():
    for brief in (_short_message_brief(), _very_short_brief(), _very_long_brief()):
        narrations = await _storyboard_narrations(brief)
        assert len(narrations) == 3
        for narration in narrations:
            stripped = narration.strip()
            assert stripped == narration, "no leading/trailing whitespace"
            assert stripped, "narration must not be empty"
            assert stripped.endswith((".", "!", "?")), "narration should end as a sentence"
            assert ".." not in stripped, "no doubled punctuation from truncation/extension"


def test_apply_duration_budget_leaves_narration_between_ideal_band_and_hard_max_untouched():
    # Narration estimated above the preferred TARGET band (14-16s) but still
    # comfortably under the 20s hard max is now *valid* and must not be
    # shortened -- only genuinely excessive narration (near the hard max)
    # should trigger the shrink path. This directly encodes the product
    # correction: 15s is a target, not a ceiling.
    brief = _very_short_brief()
    constraints = CampaignConstraints()
    _, target_max_words = narration_timing.target_word_range()
    shrink_trigger = narration_timing.shrink_trigger_word_count(constraints.max_duration_seconds)
    # A word count clearly above the ideal ceiling but clearly below the
    # shrink trigger.
    mid_word_count = (target_max_words + shrink_trigger) // 2
    assert target_max_words < mid_word_count < shrink_trigger
    scene1 = nodes.SceneNarration(required=_n_word_sentence(10), optional="You'll love it, too.")
    scene2 = nodes.SceneNarration(required=_n_word_sentence(10), optional="Explore it today.")
    remaining_words = mid_word_count - narration_timing.word_count(scene1.full_text()) - narration_timing.word_count(
        scene2.full_text()
    )
    scene3 = nodes.SceneNarration(required=_n_word_sentence(remaining_words))
    scenes = [scene1, scene2, scene3]
    expected_total = sum(narration_timing.word_count(sn.full_text()) for sn in scenes)
    assert expected_total == mid_word_count

    result = nodes._apply_duration_budget(scenes, brief, constraints)

    assert result == [sn.full_text() for sn in scenes], "optional filler must survive untouched below the trigger"


def test_apply_duration_budget_shrinks_only_optional_filler_when_unnecessarily_long():
    brief = _very_short_brief()
    constraints = CampaignConstraints()
    shrink_trigger = narration_timing.shrink_trigger_word_count(constraints.max_duration_seconds)
    scene1 = nodes.SceneNarration(required=f"Meet {brief.product_or_service}, made by {brief.business_name}.")
    scene2 = nodes.SceneNarration(required=_n_word_sentence(10), optional="You'll love it, too.")
    scene3 = nodes.SceneNarration(
        required=_n_word_sentence(10),
        optional=f"Explore {brief.product_or_service} from {brief.business_name} today.",
    )
    # Push well over the trigger using only required-side word count on
    # scene 1, so dropping every optional clause is the only lever available.
    over_budget_words = shrink_trigger + 15
    scene1_padding = _n_word_sentence(
        over_budget_words
        - narration_timing.word_count(scene1.required)
        - narration_timing.word_count(scene2.full_text())
        - narration_timing.word_count(scene3.full_text())
    )
    scene1 = nodes.SceneNarration(required=f"{scene1.required} {scene1_padding}".strip())
    scenes = [scene1, scene2, scene3]
    total_before = sum(narration_timing.word_count(sn.full_text()) for sn in scenes)
    assert total_before > shrink_trigger

    result = nodes._apply_duration_budget(scenes, brief, constraints)

    # Required content survives verbatim in every scene...
    assert scene1.required in result[0]
    assert scene2.required in result[1]
    assert scene3.required in result[2]
    # ...but the optional filler is gone (or word count is at/under trigger).
    total_after = sum(narration_timing.word_count(text) for text in result)
    assert total_after <= total_before
    assert "You'll love it, too." not in result[1]
    assert f"Explore {brief.product_or_service} from {brief.business_name} today." not in result[2]


def test_apply_duration_budget_leaves_inherently_long_required_content_alone():
    # If dropping every scene's optional filler still leaves the estimate
    # over the trigger (e.g. an inherently long business/product name baked
    # into scene 1's `required` text, which has no `optional` at all),
    # nothing further is cut -- mangling required content is never an
    # acceptable fallback.
    brief = _very_short_brief()
    constraints = CampaignConstraints()
    shrink_trigger = narration_timing.shrink_trigger_word_count(constraints.max_duration_seconds)
    huge_required = _n_word_sentence(shrink_trigger + 50)
    scenes = [
        nodes.SceneNarration(required=huge_required),
        nodes.SceneNarration(required=_n_word_sentence(5), optional="You'll love it, too."),
        nodes.SceneNarration(required=_n_word_sentence(5), optional="Explore it today."),
    ]

    result = nodes._apply_duration_budget(scenes, brief, constraints)

    assert result[0] == huge_required


def test_best_fitting_candidate_never_returns_a_truncated_phrase():
    candidates = ["It's Bold.", "A much longer candidate sentence that will not fit the budget."]
    # Room for neither candidate: must add nothing, never cut the short one down further.
    assert nodes._best_fitting_candidate(candidates, remaining_words=1, used=set()) is None
    assert nodes._best_fitting_candidate(candidates, remaining_words=0, used=set()) is None
    # Room for exactly the short candidate (2 words) but not the long one: picks it whole.
    assert nodes._best_fitting_candidate(candidates, remaining_words=2, used=set()) == "It's Bold."
    assert nodes._best_fitting_candidate(candidates, remaining_words=3, used=set()) == "It's Bold."
    # Already used: not offered again, even though it would fit.
    assert nodes._best_fitting_candidate(candidates, remaining_words=3, used={"It's Bold."}) is None


def _n_word_sentence(word_count: int) -> str:
    return " ".join(["word"] * (word_count - 1) + ["word."])


@pytest.mark.asyncio
async def test_apply_duration_budget_adds_nothing_when_only_one_to_three_words_of_room_remain():
    # _best_fitting_candidate (tested directly above) already proves a tight
    # remaining-room value never forces a truncated candidate in; this
    # exercises the same edge case through the real _apply_duration_budget
    # entry point. core_total is deliberately set 1-3 words below max_words
    # -- note that's *above* min_words here, since with a 4-word-wide target
    # band (32-36) a slot can only ever be evaluated with <=3 words of room
    # left once the running total has already reached min_words, at which
    # point the loop's own "total >= min_words" guard stops it from
    # attempting (or needing) another extension at all.
    brief = _very_short_brief()  # tone/description candidates are 4+ words: neither fits a 1-3 word gap
    constraints = CampaignConstraints()
    min_words, max_words = narration_timing.target_word_range()
    for gap in (1, 2, 3):
        core_total = max_words - gap
        assert core_total >= min_words
        core_texts = [_n_word_sentence(10), _n_word_sentence(10), _n_word_sentence(core_total - 20)]
        assert sum(narration_timing.word_count(n) for n in core_texts) == core_total
        core = [nodes.SceneNarration(required=text) for text in core_texts]

        result = nodes._apply_duration_budget(core, brief, constraints)

        assert result == core_texts, f"gap={gap}: already at/above the floor, nothing should be added or cut"


_create_creative_plan = nodes.make_create_creative_plan_node(DeterministicCreativePlanProvider())


async def _version_with_storyboard(**overrides) -> CampaignVersion:
    state: GraphState = {"version": _version(**overrides)}
    strategized = await nodes.create_strategy(state)
    copied = await nodes.generate_copy(strategized)
    result = await nodes.create_storyboard(copied)
    return result["version"]


@pytest.mark.asyncio
async def test_create_creative_plan_requires_prior_storyboard():
    state: GraphState = {"version": _version()}
    with pytest.raises(ValueError, match="create_storyboard"):
        await _create_creative_plan(state)


@pytest.mark.asyncio
async def test_create_creative_plan_produces_six_shots_from_three_scenes():
    version = await _version_with_storyboard()
    result = await _create_creative_plan({"version": version})
    plan = result["version"].creative_video_plan
    assert plan is not None
    assert len(plan.shots) == 6


@pytest.mark.asyncio
async def test_create_creative_plan_shot_numbers_are_sequential():
    version = await _version_with_storyboard()
    result = await _create_creative_plan({"version": version})
    plan = result["version"].creative_video_plan
    assert [shot.shot_number for shot in plan.shots] == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_create_creative_plan_total_duration_matches_campaign_target():
    version = await _version_with_storyboard()
    result = await _create_creative_plan({"version": version})
    plan = result["version"].creative_video_plan
    assert plan.total_duration_seconds == version.constraints.target_duration_seconds
    assert abs(sum(shot.duration_seconds for shot in plan.shots) - plan.total_duration_seconds) <= 0.01


@pytest.mark.asyncio
async def test_create_creative_plan_covers_all_three_source_scenes():
    version = await _version_with_storyboard()
    result = await _create_creative_plan({"version": version})
    plan = result["version"].creative_video_plan
    source_scenes = {shot.source_scene_number for shot in plan.shots}
    assert source_scenes == {1, 2, 3}


@pytest.mark.asyncio
async def test_create_creative_plan_uses_short_text_not_full_narration_paragraphs():
    version = await _version_with_storyboard()
    result = await _create_creative_plan({"version": version})
    plan = result["version"].creative_video_plan
    narrations = {scene.narration for scene in version.storyboard.scenes}
    texts = [shot.text for shot in plan.shots if shot.text]
    assert texts, "expected at least one shot to carry on-screen text"
    for text in texts:
        assert text not in narrations
        assert len(text) <= 200


@pytest.mark.asyncio
async def test_create_creative_plan_is_deterministic_for_identical_inputs():
    version = await _version_with_storyboard()
    first = await _create_creative_plan({"version": version})
    second = await _create_creative_plan({"version": version})
    assert first["version"].creative_video_plan == second["version"].creative_video_plan


@pytest.mark.asyncio
async def test_create_creative_plan_persists_to_campaign_version():
    version = await _version_with_storyboard()
    assert version.creative_video_plan is None
    result = await _create_creative_plan({"version": version})
    assert result["version"].creative_video_plan is not None
    assert result["version"] is not version


class _FakeStepRepositoryForGenerateImages(WorkflowRepository):
    def __init__(self, seed: dict[tuple, WorkflowStepRecord] | None = None) -> None:
        self.steps: dict[tuple, WorkflowStepRecord] = dict(seed or {})
        self.save_calls: list[WorkflowStepRecord] = []

    async def get_step(self, campaign_id: UUID, campaign_version: int, step: WorkflowStep) -> WorkflowStepRecord | None:
        return self.steps.get((campaign_id, campaign_version, step))

    async def save_step(self, record: WorkflowStepRecord, events=None) -> None:
        self.save_calls.append(record)
        self.steps[(record.campaign_id, record.campaign_version, record.step)] = record

    async def load_version(self, message):
        raise NotImplementedError

    async def acquire_lease(self, message, owner, now, expires_at):
        raise NotImplementedError

    async def heartbeat(self, message, lease, now, expires_at):
        raise NotImplementedError

    async def is_completed(self, message):
        raise NotImplementedError

    async def complete(self, message, lease, completed_at):
        raise NotImplementedError

    async def release(self, message, lease):
        raise NotImplementedError

    async def record_exhausted(self, message, receive_count, now):
        raise NotImplementedError

    async def record_invalid(self, campaign_id, code, message_id, now):
        raise NotImplementedError

    async def available(self):
        raise NotImplementedError

    async def save_version(self, version, lease, events=None):
        raise NotImplementedError


class _AlwaysFailsImageProvider(ImageProvider):
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        now = datetime.now(UTC)
        error = SanitizedWorkflowError(
            code="IMAGE_PROVIDER_UNAVAILABLE",
            message="unavailable",
            component="IMAGE_MCP",
            attempt=1,
            retryable=True,
            timestamp=now,
            correlation_id=uuid4(),
        )
        return ImageGenerationResult(
            provider="always-fails", fallback_asset=False, started_at=now, completed_at=now, error=error
        )


class _CountingMockImageProvider(ImageProvider):
    def __init__(self) -> None:
        self.calls = 0
        self._delegate = MockImageProvider()

    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.calls += 1
        return await self._delegate.generate_image(request)


@pytest.mark.asyncio
async def test_generate_images_requires_prior_storyboard():
    state: GraphState = {"version": _version()}
    node = nodes.make_generate_images_node(MockImageProvider())
    with pytest.raises(ValueError, match="create_storyboard"):
        await node(state)


@pytest.mark.asyncio
async def test_generate_images_produces_one_artifact_per_scene():
    version = await _version_with_storyboard()
    node = nodes.make_generate_images_node(MockImageProvider())
    result = await node({"version": version})

    artifacts = result["version"].image_artifacts
    assert len(artifacts) == 3
    assert [a.campaign_id for a in artifacts] == [version.campaign_id] * 3
    assert [a.campaign_version for a in artifacts] == [version.campaign_version] * 3


@pytest.mark.asyncio
async def test_generate_images_is_deterministic_with_mock_provider():
    version = await _version_with_storyboard()
    node = nodes.make_generate_images_node(MockImageProvider())

    first = await node({"version": version})
    second = await node({"version": version})

    first_checksums = [a.checksum_sha256 for a in first["version"].image_artifacts]
    second_checksums = [a.checksum_sha256 for a in second["version"].image_artifacts]
    assert first_checksums == second_checksums


@pytest.mark.asyncio
async def test_generate_images_propagates_provider_failure():
    version = await _version_with_storyboard()
    node = nodes.make_generate_images_node(_AlwaysFailsImageProvider())
    with pytest.raises(ValueError, match="image generation failed"):
        await node({"version": version})


@pytest.mark.asyncio
async def test_generate_images_is_provider_agnostic():
    version = await _version_with_storyboard()

    async def run_with(provider: ImageProvider):
        node = nodes.make_generate_images_node(provider)
        result = await node({"version": version})
        return result["version"].image_artifacts

    mock_artifacts = await run_with(MockImageProvider())
    counting_provider = _CountingMockImageProvider()
    counting_artifacts = await run_with(counting_provider)

    assert len(mock_artifacts) == len(counting_artifacts) == 3
    assert counting_provider.calls == 3


@pytest.mark.asyncio
async def test_generate_images_wrapped_with_step_tracking_runs_on_first_execution():
    version = await _version_with_storyboard()
    repository = _FakeStepRepositoryForGenerateImages()
    provider = _CountingMockImageProvider()
    wrapped = with_step_tracking(WorkflowStep.IMAGES, repository)(nodes.make_generate_images_node(provider))

    result = await wrapped({"version": version})

    assert provider.calls == 3
    assert len(result["version"].image_artifacts) == 3
    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING, StepStatus.SUCCEEDED]


@pytest.mark.asyncio
async def test_generate_images_wrapped_with_step_tracking_skips_when_already_succeeded():
    version = await _version_with_storyboard()
    now = datetime.now(UTC)
    repository = _FakeStepRepositoryForGenerateImages(
        seed={
            (version.campaign_id, version.campaign_version, WorkflowStep.IMAGES): WorkflowStepRecord(
                campaign_id=version.campaign_id,
                campaign_version=version.campaign_version,
                step=WorkflowStep.IMAGES,
                status=StepStatus.SUCCEEDED,
                created_at=now,
                updated_at=now,
            )
        }
    )
    provider = _CountingMockImageProvider()
    wrapped = with_step_tracking(WorkflowStep.IMAGES, repository)(nodes.make_generate_images_node(provider))

    result = await wrapped({"version": version})

    assert provider.calls == 0
    assert result["version"].image_artifacts == version.image_artifacts
    assert repository.save_calls == []


class _AlwaysFailsVoiceProvider(VoiceProvider):
    async def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        now = datetime.now(UTC)
        error = SanitizedWorkflowError(
            code="INTERNAL_ERROR",
            message="unavailable",
            component="UNKNOWN",
            attempt=1,
            retryable=True,
            timestamp=now,
            correlation_id=uuid4(),
        )
        return VoiceGenerationResult(
            provider="always-fails", fallback_asset=False, started_at=now, completed_at=now, error=error
        )


class _CountingMockVoiceProvider(VoiceProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.received_requests: list[VoiceGenerationRequest] = []
        self._delegate = MockVoiceProvider()

    async def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        self.received_requests.append(request)
        return await self._delegate.generate_voice(request)


@pytest.mark.asyncio
async def test_generate_voiceover_requires_prior_storyboard():
    state: GraphState = {"version": _version()}
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())
    with pytest.raises(ValueError, match="create_storyboard"):
        await node(state)


@pytest.mark.asyncio
async def test_generate_voiceover_produces_a_voice_artifact_on_the_campaign_version():
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())

    result = await node({"version": version})

    voice_artifact = result["version"].voice_artifact
    assert voice_artifact is not None
    assert voice_artifact.campaign_id == version.campaign_id
    assert voice_artifact.campaign_version == version.campaign_version
    assert voice_artifact.artifact_type.value == "AUDIO"
    assert voice_artifact.workflow_step == WorkflowStep.VOICEOVER


@pytest.mark.asyncio
async def test_generate_voiceover_persists_to_campaign_version():
    # C7 supersedes the earlier ephemeral-only design: voiceover must survive process
    # restart, duplicate delivery, and retry, which requires it live on CampaignVersion.
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())

    result = await node({"version": version})

    assert result["version"] is not version
    assert result["version"].voice_artifact is not None
    assert version.voice_artifact is None


@pytest.mark.asyncio
async def test_generate_voiceover_combines_narration_from_all_scenes():
    version = await _version_with_storyboard()
    provider = _CountingMockVoiceProvider()
    node = nodes.make_generate_voiceover_node(provider)

    await node({"version": version})

    expected_narration = " ".join(scene.narration for scene in version.storyboard.scenes)
    assert provider.received_requests[0].narration_text == expected_narration


@pytest.mark.asyncio
async def test_generate_voiceover_is_deterministic_with_mock_provider():
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(MockVoiceProvider())

    first = await node({"version": version})
    second = await node({"version": version})

    assert first["version"].voice_artifact.checksum_sha256 == second["version"].voice_artifact.checksum_sha256


@pytest.mark.asyncio
async def test_generate_voiceover_propagates_provider_failure():
    version = await _version_with_storyboard()
    node = nodes.make_generate_voiceover_node(_AlwaysFailsVoiceProvider())
    with pytest.raises(ValueError, match="voice generation failed"):
        await node({"version": version})


@pytest.mark.asyncio
async def test_generate_voiceover_is_provider_agnostic():
    version = await _version_with_storyboard()

    async def run_with(provider: VoiceProvider):
        node = nodes.make_generate_voiceover_node(provider)
        result = await node({"version": version})
        return result["version"].voice_artifact

    mock_artifact = await run_with(MockVoiceProvider())
    counting_provider = _CountingMockVoiceProvider()
    counting_artifact = await run_with(counting_provider)

    assert mock_artifact.checksum_sha256 == counting_artifact.checksum_sha256
    assert counting_provider.calls == 1


@pytest.mark.asyncio
async def test_generate_voiceover_skips_and_never_calls_the_provider_when_style_does_not_require_it():
    version = await _version_with_storyboard(
        brief=_version().brief.model_copy(update={"video_style": VideoStyle.CINEMATIC_TEXT_AD})
    )
    provider = _CountingMockVoiceProvider()
    node = nodes.make_generate_voiceover_node(provider)

    result = await node({"version": version})

    assert provider.calls == 0
    assert result["version"].voice_artifact is None
    assert result["_step_skipped"] is True
    assert result["_skip_reason"] == "video_style=CINEMATIC_TEXT_AD"


@pytest.mark.asyncio
async def test_generate_voiceover_wrapped_with_step_tracking_records_skipped_for_cinematic_text_ad():
    version = await _version_with_storyboard(
        brief=_version().brief.model_copy(update={"video_style": VideoStyle.CINEMATIC_TEXT_AD})
    )
    repository = _FakeStepRepositoryForGenerateImages()
    provider = _CountingMockVoiceProvider()
    wrapped = with_step_tracking(WorkflowStep.VOICEOVER, repository)(nodes.make_generate_voiceover_node(provider))

    result = await wrapped({"version": version})

    assert provider.calls == 0
    assert result["version"].voice_artifact is None
    assert "_step_skipped" not in result
    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING, StepStatus.SKIPPED]


@pytest.mark.asyncio
async def test_generate_voiceover_still_runs_normally_for_voiceover_ad():
    # video_style defaults to VOICEOVER_AD -- regression guard that the new branch
    # doesn't change behavior for the existing, unmodified style.
    version = await _version_with_storyboard()
    provider = _CountingMockVoiceProvider()
    node = nodes.make_generate_voiceover_node(provider)

    result = await node({"version": version})

    assert provider.calls == 1
    assert result["version"].voice_artifact is not None
    assert "_step_skipped" not in result


class _CountingVoiceAssetPipeline:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, version, is_cancelled):
        self.calls += 1
        raise AssertionError("pipeline.acquire() must not be called when voiceover is skipped")


@pytest.mark.asyncio
async def test_acquire_voiceover_skips_and_never_calls_the_pipeline_when_style_does_not_require_it():
    version = await _version_with_storyboard(
        brief=_version().brief.model_copy(update={"video_style": VideoStyle.CINEMATIC_TEXT_AD})
    )
    pipeline = _CountingVoiceAssetPipeline()

    async def never_cancelled() -> bool:
        return False

    node = nodes.make_acquire_voiceover_node(pipeline, never_cancelled)
    result = await node({"version": version})

    assert pipeline.calls == 0
    assert result["version"].voice_artifact is None
    assert result["_step_skipped"] is True
    assert result["_skip_reason"] == "video_style=CINEMATIC_TEXT_AD"


@pytest.mark.asyncio
async def test_generate_voiceover_wrapped_with_step_tracking_runs_on_first_execution():
    version = await _version_with_storyboard()
    repository = _FakeStepRepositoryForGenerateImages()
    provider = _CountingMockVoiceProvider()
    wrapped = with_step_tracking(WorkflowStep.VOICEOVER, repository)(nodes.make_generate_voiceover_node(provider))

    result = await wrapped({"version": version})

    assert provider.calls == 1
    assert result["version"].voice_artifact is not None
    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING, StepStatus.SUCCEEDED]


@pytest.mark.asyncio
async def test_generate_voiceover_wrapped_with_step_tracking_skips_when_already_succeeded():
    version = await _version_with_storyboard()
    now = datetime.now(UTC)
    repository = _FakeStepRepositoryForGenerateImages(
        seed={
            (version.campaign_id, version.campaign_version, WorkflowStep.VOICEOVER): WorkflowStepRecord(
                campaign_id=version.campaign_id,
                campaign_version=version.campaign_version,
                step=WorkflowStep.VOICEOVER,
                status=StepStatus.SUCCEEDED,
                created_at=now,
                updated_at=now,
            )
        }
    )
    provider = _CountingMockVoiceProvider()
    wrapped = with_step_tracking(WorkflowStep.VOICEOVER, repository)(nodes.make_generate_voiceover_node(provider))

    result = await wrapped({"version": version})

    # Not called at all: duplicate SQS delivery (or any redelivery once VOICEOVER already
    # succeeded for this campaign_version) must not trigger a second Polly synthesis.
    assert provider.calls == 0
    assert result["version"].voice_artifact == version.voice_artifact
    assert repository.save_calls == []


async def _state_with_images_and_voice() -> GraphState:
    version = await _version_with_storyboard()
    images_result = await nodes.make_generate_images_node(MockImageProvider())({"version": version})
    voice_result = await nodes.make_generate_voiceover_node(MockVoiceProvider())({"version": images_result["version"]})
    return {"version": voice_result["version"]}


class _AlwaysFailsVideoProvider(VideoProvider):
    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult:
        now = datetime.now(UTC)
        error = SanitizedWorkflowError(
            code="VIDEO_PROVIDER_UNAVAILABLE",
            message="unavailable",
            component="UNKNOWN",
            attempt=1,
            retryable=True,
            timestamp=now,
            correlation_id=uuid4(),
        )
        return VideoRenderResult(
            provider="always-fails", fallback_asset=False, started_at=now, completed_at=now, error=error
        )


class _CountingMockVideoProvider(VideoProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.received_requests: list[VideoRenderRequest] = []
        self._delegate = MockVideoProvider()

    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult:
        self.calls += 1
        self.received_requests.append(request)
        return await self._delegate.render_video(request)


@pytest.mark.asyncio
async def test_render_video_requires_prior_storyboard():
    state: GraphState = {"version": _version()}
    node = nodes.make_render_video_node(MockVideoProvider())
    with pytest.raises(ValueError, match="create_storyboard"):
        await node(state)


@pytest.mark.asyncio
async def test_render_video_requires_prior_generate_images():
    version = await _version_with_storyboard()
    voice_result = await nodes.make_generate_voiceover_node(MockVoiceProvider())({"version": version})
    state: GraphState = {"version": voice_result["version"]}
    node = nodes.make_render_video_node(MockVideoProvider())
    with pytest.raises(ValueError, match="generate_images"):
        await node(state)


@pytest.mark.asyncio
async def test_render_video_requires_prior_generate_voiceover():
    version = await _version_with_storyboard()
    images_result = await nodes.make_generate_images_node(MockImageProvider())({"version": version})
    node = nodes.make_render_video_node(MockVideoProvider())
    with pytest.raises(ValueError, match="generate_voiceover"):
        await node({"version": images_result["version"]})


@pytest.mark.asyncio
async def test_render_video_produces_video_artifact_persisted_on_campaign_version():
    state = await _state_with_images_and_voice()
    node = nodes.make_render_video_node(MockVideoProvider())

    result = await node(state)

    video_artifact = result["version"].video_artifact
    assert video_artifact is not None
    assert video_artifact.campaign_id == state["version"].campaign_id
    assert video_artifact.campaign_version == state["version"].campaign_version


@pytest.mark.asyncio
async def test_render_video_forwards_voice_artifact_to_the_provider_request():
    state = await _state_with_images_and_voice()
    provider = _CountingMockVideoProvider()
    node = nodes.make_render_video_node(provider)

    await node(state)

    assert provider.received_requests[0].voice_artifact == state["version"].voice_artifact


@pytest.mark.asyncio
async def test_render_video_is_deterministic_with_mock_provider():
    state = await _state_with_images_and_voice()
    node = nodes.make_render_video_node(MockVideoProvider())

    first = await node(state)
    second = await node(state)

    assert first["version"].video_artifact.checksum_sha256 == second["version"].video_artifact.checksum_sha256


@pytest.mark.asyncio
async def test_render_video_propagates_provider_failure():
    state = await _state_with_images_and_voice()
    node = nodes.make_render_video_node(_AlwaysFailsVideoProvider())
    with pytest.raises(ValueError, match="video rendering failed"):
        await node(state)


@pytest.mark.asyncio
async def test_render_video_is_provider_agnostic():
    state = await _state_with_images_and_voice()

    async def run_with(provider: VideoProvider):
        node = nodes.make_render_video_node(provider)
        result = await node(state)
        return result["version"].video_artifact

    mock_artifact = await run_with(MockVideoProvider())
    counting_provider = _CountingMockVideoProvider()
    counting_artifact = await run_with(counting_provider)

    assert mock_artifact.checksum_sha256 == counting_artifact.checksum_sha256
    assert counting_provider.calls == 1


@pytest.mark.asyncio
async def test_render_video_wrapped_with_step_tracking_runs_on_first_execution():
    state = await _state_with_images_and_voice()
    repository = _FakeStepRepositoryForGenerateImages()
    provider = _CountingMockVideoProvider()
    wrapped = with_step_tracking(WorkflowStep.VIDEO, repository)(nodes.make_render_video_node(provider))

    result = await wrapped(state)

    assert provider.calls == 1
    assert result["version"].video_artifact is not None
    assert [record.status for record in repository.save_calls] == [StepStatus.RUNNING, StepStatus.SUCCEEDED]


@pytest.mark.asyncio
async def test_render_video_wrapped_with_step_tracking_skips_when_already_succeeded():
    state = await _state_with_images_and_voice()
    version = state["version"]
    now = datetime.now(UTC)
    repository = _FakeStepRepositoryForGenerateImages(
        seed={
            (version.campaign_id, version.campaign_version, WorkflowStep.VIDEO): WorkflowStepRecord(
                campaign_id=version.campaign_id,
                campaign_version=version.campaign_version,
                step=WorkflowStep.VIDEO,
                status=StepStatus.SUCCEEDED,
                created_at=now,
                updated_at=now,
            )
        }
    )
    provider = _CountingMockVideoProvider()
    wrapped = with_step_tracking(WorkflowStep.VIDEO, repository)(nodes.make_render_video_node(provider))

    result = await wrapped(state)

    assert provider.calls == 0
    assert result["version"].video_artifact == version.video_artifact
    assert repository.save_calls == []


async def _state_with_full_review_package() -> GraphState:
    state = await _state_with_images_and_voice()
    render_node = nodes.make_render_video_node(MockVideoProvider())
    rendered = await render_node(state)
    return {"version": rendered["version"]}


@pytest.mark.asyncio
async def test_validate_review_package_passes_when_all_artifacts_present():
    state = await _state_with_full_review_package()

    result = await nodes.validate_review_package(state)

    validation = result["review_validation"]
    assert validation.is_valid is True
    assert validation.missing_artifacts == []


@pytest.mark.asyncio
async def test_validate_review_package_reports_all_missing_artifacts_for_a_fresh_version():
    state: GraphState = {"version": _version()}

    result = await nodes.validate_review_package(state)

    validation = result["review_validation"]
    assert validation.is_valid is False
    assert validation.missing_artifacts == [
        "strategy",
        "campaign_copy",
        "storyboard",
        "image_artifacts",
        "voice_artifact",
        "video_artifact",
    ]


@pytest.mark.asyncio
async def test_validate_review_package_reports_only_missing_voice_artifact():
    state = await _state_with_full_review_package()
    # voice_artifact now lives on CampaignVersion, so isolate validate_review_package's
    # own field-by-field logic by clearing just that one field on an otherwise-complete version.
    version = state["version"].model_copy(update={"voice_artifact": None})

    result = await nodes.validate_review_package({"version": version})

    validation = result["review_validation"]
    assert validation.is_valid is False
    assert validation.missing_artifacts == ["voice_artifact"]


@pytest.mark.asyncio
async def test_validate_review_package_does_not_require_voice_artifact_for_cinematic_text_ad():
    state = await _state_with_full_review_package()
    version = state["version"].model_copy(
        update={
            "voice_artifact": None,
            "brief": state["version"].brief.model_copy(update={"video_style": VideoStyle.CINEMATIC_TEXT_AD}),
        }
    )

    result = await nodes.validate_review_package({"version": version})

    validation = result["review_validation"]
    assert validation.is_valid is True
    assert validation.missing_artifacts == []


@pytest.mark.asyncio
async def test_validate_review_package_reports_only_missing_video_artifact():
    state = await _state_with_images_and_voice()  # no render_video run yet

    result = await nodes.validate_review_package(state)

    validation = result["review_validation"]
    assert validation.is_valid is False
    assert validation.missing_artifacts == ["video_artifact"]


@pytest.mark.asyncio
async def test_validate_review_package_preserves_existing_state_keys():
    state = await _state_with_full_review_package()

    result = await nodes.validate_review_package(state)

    assert result["version"] is state["version"]
    assert result["version"].voice_artifact is state["version"].voice_artifact


async def _state_with_valid_review_package() -> GraphState:
    state = await _state_with_full_review_package()
    return await nodes.validate_review_package(state)


@pytest.mark.asyncio
async def test_await_human_approval_requires_prior_validate_review_package():
    state = await _state_with_full_review_package()
    with pytest.raises(ValueError, match="validate_review_package"):
        await nodes.await_human_approval(state)


@pytest.mark.asyncio
async def test_await_human_approval_rejects_an_incomplete_review_package():
    state: GraphState = {"version": _version()}
    validated = await nodes.validate_review_package(state)
    with pytest.raises(ValueError, match="incomplete"):
        await nodes.await_human_approval(validated)


@pytest.mark.asyncio
async def test_await_human_approval_sets_status_ready_for_review():
    state = await _state_with_valid_review_package()

    result = await nodes.await_human_approval(state)

    assert result["version"].status == CampaignStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_await_human_approval_preserves_all_other_version_fields():
    state = await _state_with_valid_review_package()

    result = await nodes.await_human_approval(state)

    before = state["version"].model_dump(exclude={"status", "review_package"})
    after = result["version"].model_dump(exclude={"status", "review_package"})
    assert before == after


@pytest.mark.asyncio
async def test_await_human_approval_sets_a_deterministic_review_package():
    state = await _state_with_valid_review_package()

    first = await nodes.await_human_approval(state)
    second = await nodes.await_human_approval(state)

    assert first["version"].review_package is not None
    assert first["version"].review_package == second["version"].review_package
    expected_ids = {artifact.artifact_id for artifact in state["version"].image_artifacts}
    expected_ids.add(state["version"].video_artifact.artifact_id)
    assert set(first["version"].review_package.artifact_ids) == expected_ids
    assert first["version"].review_package.artifact_id == nodes.deterministic_review_package_artifact_id(
        state["version"].campaign_id, state["version"].campaign_version
    )


async def _final_version() -> CampaignVersion:
    state = await _state_with_images_and_voice()
    render_node = nodes.make_render_video_node(MockVideoProvider())
    rendered = await render_node(state)
    return rendered["version"]


async def _final_version_with_review_package() -> CampaignVersion:
    version = await _final_version()
    validated = await nodes.validate_review_package({"version": version})
    approved = await nodes.await_human_approval(validated)
    return approved["version"]


async def _never_cancelled() -> bool:
    return False


class _RecordingPackagePipeline:
    def __init__(self) -> None:
        self.calls: list[CampaignVersion] = []

    async def acquire(self, version: CampaignVersion, is_cancelled):
        self.calls.append(version)
        from campaign_contracts.artifacts import FinalPackageArtifactReference

        return FinalPackageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.PACKAGE,
            mime_type="application/zip",
            size_bytes=1024,
            checksum_sha256="c" * 64,
            created_at=datetime.now(UTC),
            provider="test-package-pipeline",
        )


@pytest.mark.asyncio
async def test_prepare_final_package_requires_strategy_copy_and_storyboard():
    state: GraphState = {"version": _version()}
    node = nodes.make_prepare_final_package_node(_RecordingPackagePipeline(), _never_cancelled)
    with pytest.raises(ValueError, match="strategy"):
        await node(state)


@pytest.mark.asyncio
async def test_prepare_final_package_requires_prior_generate_images():
    version = await _version_with_storyboard()
    node = nodes.make_prepare_final_package_node(_RecordingPackagePipeline(), _never_cancelled)
    with pytest.raises(ValueError, match="generate_images"):
        await node({"version": version})


@pytest.mark.asyncio
async def test_prepare_final_package_requires_prior_render_video():
    version = await _version_with_storyboard()
    images_result = await nodes.make_generate_images_node(MockImageProvider())({"version": version})
    node = nodes.make_prepare_final_package_node(_RecordingPackagePipeline(), _never_cancelled)
    with pytest.raises(ValueError, match="render_video"):
        await node({"version": images_result["version"]})


@pytest.mark.asyncio
async def test_prepare_final_package_requires_a_review_package_established_before_approval():
    version = await _final_version()
    node = nodes.make_prepare_final_package_node(_RecordingPackagePipeline(), _never_cancelled)
    with pytest.raises(ValueError, match="review package"):
        await node({"version": version})


@pytest.mark.asyncio
async def test_prepare_final_package_sets_status_final():
    version = await _final_version_with_review_package()
    node = nodes.make_prepare_final_package_node(_RecordingPackagePipeline(), _never_cancelled)

    result = await node({"version": version})

    assert result["version"].status == CampaignStatus.FINAL


@pytest.mark.asyncio
async def test_prepare_final_package_persists_the_package_artifact_from_the_pipeline():
    version = await _final_version_with_review_package()
    pipeline = _RecordingPackagePipeline()
    node = nodes.make_prepare_final_package_node(pipeline, _never_cancelled)

    result = await node({"version": version})

    assert pipeline.calls == [version]
    assert result["version"].package_artifact is not None
    assert result["version"].package_artifact.checksum_sha256 == "c" * 64


@pytest.mark.asyncio
async def test_prepare_final_package_does_not_regenerate_any_artifacts():
    version = await _final_version_with_review_package()
    node = nodes.make_prepare_final_package_node(_RecordingPackagePipeline(), _never_cancelled)

    result = await node({"version": version})

    assert result["version"].image_artifacts == version.image_artifacts
    assert result["version"].video_artifact == version.video_artifact


@pytest.mark.asyncio
async def test_await_human_approval_never_loops_sleeps_or_polls():
    source = inspect.getsource(nodes.await_human_approval)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.While, ast.For, ast.AsyncFor)), (
            f"await_human_approval must not loop/poll/retry, found {type(node).__name__}"
        )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "sleep", "await_human_approval must not busy-wait"


@pytest.mark.asyncio
async def test_handle_failure_transitions_to_failed_for_a_generic_error():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    assert result["version"].status == CampaignStatus.FAILED


@pytest.mark.asyncio
async def test_handle_failure_transitions_to_cancelled_for_node_cancelled():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, NodeCancelled("video"), step=WorkflowStep.VIDEO)

    assert result["version"].status == CampaignStatus.CANCELLED


@pytest.mark.asyncio
async def test_handle_failure_marks_retryable_when_budget_remains():
    version = await _final_version()
    version = version.model_copy(update={"retry": RetryMetadata(attempt=0, max_attempts=3)})
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("transient"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.attempt == 1
    assert result["version"].retry.retryable is True
    assert result["version"].error.code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_handle_failure_marks_not_retryable_when_budget_exhausted():
    version = await _final_version()
    version = version.model_copy(update={"retry": RetryMetadata(attempt=2, max_attempts=3)})
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("still failing"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.attempt == 3
    assert result["version"].retry.retryable is False
    assert result["version"].error.code == "RETRY_EXHAUSTED"


@pytest.mark.asyncio
async def test_handle_failure_cancellation_is_never_retryable_regardless_of_budget():
    version = await _final_version()
    version = version.model_copy(update={"retry": RetryMetadata(attempt=0, max_attempts=3)})
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, NodeCancelled("video"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.retryable is False
    assert result["version"].retry.attempt == 0
    assert result["version"].error.code == "CANCELLED_BY_USER"


@pytest.mark.asyncio
async def test_handle_failure_records_resume_step():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    assert result["version"].retry.resume_step == WorkflowStep.VIDEO
    assert result["version"].error.workflow_step == WorkflowStep.VIDEO


@pytest.mark.asyncio
async def test_handle_failure_records_a_sanitized_error_with_the_message_and_component():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    error = result["version"].error
    assert error is not None
    assert error.message == "boom"
    assert error.component == ErrorComponent.LANGGRAPH_WORKER
    assert error.campaign_id == version.campaign_id
    assert error.campaign_version == version.campaign_version


@pytest.mark.asyncio
async def test_handle_failure_preserves_all_existing_content():
    version = await _final_version()
    state: GraphState = {"version": version}

    result = await nodes.handle_failure(state, ValueError("boom"), step=WorkflowStep.VIDEO)

    updated = result["version"]
    assert updated.strategy == version.strategy
    assert updated.campaign_copy == version.campaign_copy
    assert updated.storyboard == version.storyboard
    assert updated.image_artifacts == version.image_artifacts
    assert updated.video_artifact == version.video_artifact


@pytest.mark.asyncio
async def test_handle_failure_never_loops_sleeps_or_retries():
    source = inspect.getsource(nodes.handle_failure)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.While, ast.For, ast.AsyncFor)), (
            f"handle_failure must not loop/poll/retry, found {type(node).__name__}"
        )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "sleep", "handle_failure must not busy-wait"
