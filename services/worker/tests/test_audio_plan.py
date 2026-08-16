import pytest
from campaign_contracts.enums import VideoStyle

from campaign_worker.video.audio_plan import AudioPlan, audio_plan_for


def test_voiceover_ad_requires_voiceover_only():
    plan = audio_plan_for(VideoStyle.VOICEOVER_AD)
    assert plan == AudioPlan(voiceover_required=True, music_required=False, sfx_enabled=False)


def test_cinematic_text_ad_requires_music_and_sfx_not_voiceover():
    plan = audio_plan_for(VideoStyle.CINEMATIC_TEXT_AD)
    assert plan == AudioPlan(voiceover_required=False, music_required=True, sfx_enabled=True)


def test_music_first_reel_is_reserved_and_raises():
    with pytest.raises(NotImplementedError):
        audio_plan_for(VideoStyle.MUSIC_FIRST_REEL)
