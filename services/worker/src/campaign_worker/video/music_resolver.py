"""Resolves a single music asset for CINEMATIC_TEXT_AD.

There is no AI music-selection provider yet. Resolution is isolated behind
this module so a later provider/library-backed selector can plug in without
redesigning AudioMixRequest or the video pipeline -- today the only
resolvable source is a single configured local/dev path (CINEMATIC_MUSIC_PATH),
but mood scoring already exists and is tested so future multi-track
selection is a resolver-internal change, not a pipeline-shape change.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from campaign_contracts.campaign import CampaignVersion


@dataclass(frozen=True, slots=True)
class ResolvedMusicAsset:
    path: Path
    source: str
    checksum_sha256: str
    mood: str | None = None


# Ordered so earlier moods win when multiple keyword sets match (e.g. a tone
# combining "premium" and "chill" resolves to "premium" -- the more
# brand-forward mood is preferred as a deterministic tiebreak).
_MOOD_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cinematic", ("cinematic", "dramatic", "epic")),
    ("premium", ("premium", "luxury", "elevated", "refined")),
    ("energetic", ("energetic", "bold", "dynamic", "vibrant")),
    ("playful", ("playful", "fun", "quirky")),
    ("chill", ("chill", "calm", "relaxed", "soft")),
    ("modern", ("modern", "clean", "minimal", "sleek")),
)
_DEFAULT_MOOD = "modern"


def resolve_music_mood(version: CampaignVersion) -> str:
    text_parts = [version.brief.tone]
    plan = version.creative_video_plan
    if plan is not None:
        text_parts.append(plan.visual_style)
    haystack = " ".join(text_parts).lower()
    for mood, keywords in _MOOD_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return mood
    return _DEFAULT_MOOD


def resolve_music_asset(version: CampaignVersion, *, configured_path: Path | None) -> ResolvedMusicAsset | None:
    if configured_path is None:
        return None
    checksum = hashlib.sha256(configured_path.read_bytes()).hexdigest()
    return ResolvedMusicAsset(
        path=configured_path,
        source="local-configured",
        checksum_sha256=checksum,
        mood=resolve_music_mood(version),
    )
