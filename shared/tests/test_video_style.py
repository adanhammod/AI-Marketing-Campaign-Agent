import json
from pathlib import Path

from campaign_contracts.campaign import CampaignVersion, NormalizedCampaignBrief
from campaign_contracts.enums import VideoStyle

ROOT = Path(__file__).parents[1]
VALID = ROOT / "fixtures" / "valid"


def _brief(**overrides):
    defaults = dict(
        business_name="Luna Coffee",
        product_or_service="Luna Cold Brew",
        business_description="A local roaster offering weekly cold brew delivery to city cafes.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
    )
    defaults.update(overrides)
    return NormalizedCampaignBrief(**defaults)


def test_video_style_has_the_three_expected_members():
    assert VideoStyle.VOICEOVER_AD == "VOICEOVER_AD"
    assert VideoStyle.CINEMATIC_TEXT_AD == "CINEMATIC_TEXT_AD"
    assert VideoStyle.MUSIC_FIRST_REEL == "MUSIC_FIRST_REEL"


def test_normalized_campaign_brief_defaults_video_style_to_voiceover_ad():
    brief = _brief()
    assert brief.video_style == VideoStyle.VOICEOVER_AD


def test_normalized_campaign_brief_accepts_explicit_cinematic_text_ad():
    brief = _brief(video_style=VideoStyle.CINEMATIC_TEXT_AD)
    assert brief.video_style == VideoStyle.CINEMATIC_TEXT_AD


def test_existing_campaign_fixture_without_video_style_key_defaults_to_voiceover_ad():
    # Regression guard: already-persisted campaigns (and existing fixtures) have no
    # video_style key at all -- backward compatibility depends on the default applying.
    payload = json.loads((VALID / "ready-for-review.json").read_text(encoding="utf-8-sig"))
    assert "video_style" not in payload["brief"]
    version = CampaignVersion.model_validate(payload)
    assert version.brief.video_style == VideoStyle.VOICEOVER_AD
