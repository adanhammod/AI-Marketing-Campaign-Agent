"""Worker-internal audio requirements per VideoStyle.

Not a shared campaign_contracts model: fully derivable from VideoStyle, so
there is nothing here worth persisting or versioning independently.
"""

from dataclasses import dataclass

from campaign_contracts.enums import VideoStyle


@dataclass(frozen=True, slots=True)
class AudioPlan:
    voiceover_required: bool
    music_required: bool
    # Best-effort background music underneath the voiceover: resolved if
    # configured/readable, but never fails the campaign if it isn't (unlike
    # music_required, which is a hard failure). Mutually exclusive with
    # music_required in practice -- a style never sets both.
    music_optional: bool
    sfx_enabled: bool


def audio_plan_for(style: VideoStyle) -> AudioPlan:
    if style is VideoStyle.VOICEOVER_AD:
        return AudioPlan(voiceover_required=True, music_required=False, music_optional=True, sfx_enabled=False)
    if style is VideoStyle.CINEMATIC_TEXT_AD:
        return AudioPlan(voiceover_required=False, music_required=True, music_optional=False, sfx_enabled=True)
    raise NotImplementedError(f"{style} is reserved and not yet implemented")
