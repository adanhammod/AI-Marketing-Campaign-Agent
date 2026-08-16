import hashlib
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
    VideoShot,
)
from campaign_contracts.enums import AssetRole, CameraMotion, CampaignStatus, ShotRole, TransitionType, VideoStyle

from campaign_worker.video.music_resolver import ResolvedMusicAsset, resolve_music_asset, resolve_music_mood


def _storyboard() -> Storyboard:
    return Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration="n",
                visual_prompt=f"scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )


def _plan(visual_style: str) -> CreativeVideoPlan:
    shot = VideoShot(
        shot_number=1,
        role=ShotRole.HOOK,
        source_scene_number=1,
        asset_role=AssetRole.HERO_PRODUCT,
        visual_description="d",
        duration_seconds=15.0,
        camera_motion=CameraMotion.STATIC,
        transition_in=TransitionType.CUT,
    )
    return CreativeVideoPlan(concept="c", visual_style=visual_style, total_duration_seconds=15, shots=[shot])


def _version(*, tone: str = "bright", visual_style: str | None = None) -> CampaignVersion:
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase sales",
        platforms=["instagram"],
        tone=tone,
        language="en-US",
        target_audience="Urban professionals",
        video_style=VideoStyle.CINEMATIC_TEXT_AD,
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
        storyboard=_storyboard(),
        creative_video_plan=_plan(visual_style) if visual_style else None,
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )


# ---------------------------------------------------------------------------
# resolve_music_mood
# ---------------------------------------------------------------------------


def test_mood_resolves_from_visual_style_keywords():
    version = _version(tone="bright", visual_style="cinematic, dramatic, epic")
    assert resolve_music_mood(version) == "cinematic"


def test_mood_resolves_from_tone_when_no_plan():
    version = _version(tone="playful and fun", visual_style=None)
    assert resolve_music_mood(version) == "playful"


def test_mood_falls_back_to_modern_when_no_keywords_match():
    version = _version(tone="neutral", visual_style="asdf qwerty")
    assert resolve_music_mood(version) == "modern"


def test_mood_resolution_is_deterministic():
    version = _version(tone="bright", visual_style="energetic, bold, premium")
    assert resolve_music_mood(version) == resolve_music_mood(version)


# ---------------------------------------------------------------------------
# resolve_music_asset
# ---------------------------------------------------------------------------


def test_resolve_music_asset_returns_none_when_no_configured_path():
    version = _version()
    assert resolve_music_asset(version, configured_path=None) is None


def test_resolve_music_asset_uses_configured_local_path(tmp_path):
    music_file = tmp_path / "bed.wav"
    music_file.write_bytes(b"fake-music-bytes")
    version = _version(visual_style="warm, energetic, upbeat")

    resolved = resolve_music_asset(version, configured_path=music_file)

    assert isinstance(resolved, ResolvedMusicAsset)
    assert resolved.path == music_file
    assert resolved.source == "local-configured"
    assert resolved.mood == "energetic"
    assert resolved.checksum_sha256 == hashlib.sha256(b"fake-music-bytes").hexdigest()


def test_resolve_music_asset_checksum_changes_with_content(tmp_path):
    music_file = tmp_path / "bed.wav"
    music_file.write_bytes(b"content-a")
    version = _version()
    first = resolve_music_asset(version, configured_path=music_file)

    music_file.write_bytes(b"content-b")
    second = resolve_music_asset(version, configured_path=music_file)

    assert first.checksum_sha256 != second.checksum_sha256
