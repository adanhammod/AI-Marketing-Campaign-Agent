import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, CreativeVideoPlan, VideoShot
from campaign_contracts.enums import AssetRole, AudioCueType, CameraMotion, ShotRole, TransitionType

ROOT = Path(__file__).parents[1]
VALID = ROOT / "fixtures" / "valid"


def _shot(**overrides) -> VideoShot:
    defaults = dict(
        shot_number=1,
        role=ShotRole.HOOK,
        source_scene_number=1,
        asset_role=AssetRole.HERO_PRODUCT,
        visual_description="Close crop on the product, fast push-in.",
        duration_seconds=2.0,
        text="YOUR 3PM RESET.",
        camera_motion=CameraMotion.PUSH_IN,
        transition_in=TransitionType.CUT,
    )
    defaults.update(overrides)
    return VideoShot(**defaults)


def _plan(**overrides) -> CreativeVideoPlan:
    shots = overrides.pop("shots", None) or [
        _shot(shot_number=1, duration_seconds=7.5),
        _shot(shot_number=2, duration_seconds=7.5, text=None),
    ]
    defaults = dict(
        concept="YOUR 3PM RESET",
        visual_style="modern, energetic, premium",
        total_duration_seconds=overrides.pop("total_duration_seconds", 15),
        shots=shots,
    )
    defaults.update(overrides)
    return CreativeVideoPlan(**defaults)


def _campaign_version(**overrides) -> CampaignVersion:
    from datetime import UTC, datetime
    from uuid import uuid4

    from campaign_contracts.api import CampaignCreationRequest
    from campaign_contracts.enums import CampaignStatus

    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Luna Coffee",
        product_or_service="Luna Cold Brew",
        business_description="A local roaster offering weekly cold brew delivery to city cafes.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )
    defaults = dict(
        campaign_id=uuid4(),
        campaign_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        retry={"attempt": 0, "max_attempts": 3, "retryable": False, "resume_step": None},
        created_at=now,
        updated_at=now,
        lock_version=1,
    )
    defaults.update(overrides)
    return CampaignVersion(**defaults)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_shot_role_has_the_expected_members():
    assert {m.value for m in ShotRole} == {
        "HOOK", "PRODUCT_HERO", "ACTION", "DETAIL", "LIFESTYLE", "MESSAGE", "PAYOFF", "CTA",
    }


def test_asset_role_has_the_expected_members():
    assert {m.value for m in AssetRole} == {
        "HERO_PRODUCT", "ACTION_SHOT", "DETAIL_SHOT", "LIFESTYLE_PRODUCT", "CTA_FRAME",
    }


def test_camera_motion_has_the_expected_members():
    assert {m.value for m in CameraMotion} == {
        "STATIC", "PUSH_IN", "PULL_OUT", "PAN_LEFT", "PAN_RIGHT", "PAN_UP", "PAN_DOWN", "MACRO_PUSH", "SCALE_THROUGH",
    }


def test_transition_type_has_the_expected_members():
    assert {m.value for m in TransitionType} == {
        "CUT", "CROSSFADE", "WIPE", "MASK_REVEAL", "SCALE_THROUGH", "MOTION_MATCH",
    }


def test_audio_cue_type_has_the_expected_members():
    assert {m.value for m in AudioCueType} == {"TRANSITION_HIT", "IMPACT", "WHOOSH", "BRAND_HIT", "ICE_CLINK"}


# ---------------------------------------------------------------------------
# VideoShot
# ---------------------------------------------------------------------------


def test_video_shot_constructs_with_valid_data():
    shot = _shot()
    assert shot.role == ShotRole.HOOK
    assert shot.audio_cues == []


def test_video_shot_rejects_non_positive_duration():
    with pytest.raises(ValidationError):
        _shot(duration_seconds=0)
    with pytest.raises(ValidationError):
        _shot(duration_seconds=-1.5)


def test_video_shot_rejects_source_scene_number_outside_one_to_three():
    with pytest.raises(ValidationError):
        _shot(source_scene_number=0)
    with pytest.raises(ValidationError):
        _shot(source_scene_number=4)


# ---------------------------------------------------------------------------
# CreativeVideoPlan
# ---------------------------------------------------------------------------


def test_creative_video_plan_constructs_when_shots_are_sequential_and_sum_matches():
    plan = _plan()
    assert plan.concept == "YOUR 3PM RESET"
    assert len(plan.shots) == 2


def test_creative_video_plan_rejects_non_sequential_shot_numbers():
    with pytest.raises(ValidationError, match="sequential"):
        _plan(shots=[_shot(shot_number=1, duration_seconds=7.5), _shot(shot_number=3, duration_seconds=7.5)])


def test_creative_video_plan_rejects_out_of_order_shot_numbers():
    with pytest.raises(ValidationError, match="sequential"):
        _plan(shots=[_shot(shot_number=2, duration_seconds=7.5), _shot(shot_number=1, duration_seconds=7.5)])


def test_creative_video_plan_rejects_duration_sum_mismatch():
    with pytest.raises(ValidationError, match="sum"):
        _plan(
            total_duration_seconds=15,
            shots=[_shot(shot_number=1, duration_seconds=5.0), _shot(shot_number=2, duration_seconds=5.0)],
        )


def test_creative_video_plan_accepts_float_shot_durations_summing_within_tolerance():
    # Regression guard: 8 shots at .1s-precision durations must not fail purely
    # from binary float representation when they sum to the int total.
    durations = [1.2, 2.0, 1.0, 2.5, 1.8, 2.0, 2.5, 2.0]
    shots = [_shot(shot_number=i + 1, duration_seconds=d, text=None) for i, d in enumerate(durations)]
    plan = _plan(total_duration_seconds=15, shots=shots)
    assert len(plan.shots) == 8


# ---------------------------------------------------------------------------
# CampaignVersion integration
# ---------------------------------------------------------------------------


def test_campaign_version_accepts_creative_video_plan_none_by_default():
    version = _campaign_version()
    assert version.creative_video_plan is None


def test_campaign_version_accepts_a_populated_creative_video_plan():
    version = _campaign_version(creative_video_plan=_plan())
    assert version.creative_video_plan is not None
    assert version.creative_video_plan.concept == "YOUR 3PM RESET"


def test_campaign_version_rejects_creative_video_plan_duration_outside_constraints():
    # CampaignConstraints() defaults to a locked 13-17s band.
    with pytest.raises(ValidationError, match="constraints"):
        _campaign_version(
            creative_video_plan=_plan(
                total_duration_seconds=25,
                shots=[_shot(shot_number=1, duration_seconds=25.0, text=None)],
            )
        )


def test_existing_campaign_fixture_without_creative_video_plan_key_defaults_to_none():
    payload = json.loads((VALID / "ready-for-review.json").read_text(encoding="utf-8-sig"))
    assert "creative_video_plan" not in payload
    version = CampaignVersion.model_validate(payload)
    assert version.creative_video_plan is None
