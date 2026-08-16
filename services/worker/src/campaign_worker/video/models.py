from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from campaign_contracts.campaign import Storyboard
from campaign_contracts.enums import AudioCueType


@dataclass(frozen=True, slots=True)
class RenderedVideo:
    data: bytes
    checksum_sha256: str
    width: int
    height: int
    duration_seconds: float
    video_codec: str
    audio_codec: str
    fps: int


@dataclass(frozen=True, slots=True)
class TextCue:
    text: str
    start_seconds: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ResolvedVideoShot:
    """Renderer-facing projection of a single CreativeVideoPlan VideoShot.

    No start_seconds field -- the renderer derives cumulative shot start times
    by summing duration_seconds in list order, so there is exactly one place
    that computes timing from this list, not a second stored value that could
    drift from it.
    """

    shot_number: int
    scene_number: int
    duration_seconds: float
    text: str | None
    camera_motion_key: str
    transition_key: str
    # Semantic audio cue intent carried through from VideoShot.audio_cues, for
    # audio_cue_library to resolve into timed SfxCues. Empty by default so
    # existing positional/keyword construction sites (Slice 3) stay valid.
    audio_cues: tuple[AudioCueType, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalRenderRequest:
    """Pure, already-resolved local render inputs -- no S3/DynamoDB knowledge.

    scene_image_paths and scene_durations are keyed by scene_number (never
    array order), matching how PublicArtifactReference.scene_number is the
    canonical mapping key everywhere else in the video pipeline.
    """

    scene_image_paths: dict[int, Path]
    scene_durations: dict[int, float]
    audio_path: Path
    storyboard: Storyboard
    headline: str
    key_message: str
    cta: str
    output_path: Path
    width: int
    height: int
    fps: int
    # Short on-screen text cues (e.g. for a narration-free CINEMATIC_TEXT_AD
    # render). Empty by default -- the composer falls back to headline/
    # key_message/cta, preserving today's behavior unchanged when unset.
    text_cues: list[TextCue] = field(default_factory=list)
    # Renderer-facing shots resolved from a persisted CreativeVideoPlan. Empty by
    # default -- the composer falls back to its legacy hardcoded shot plan when
    # unset, preserving today's behavior unchanged for historical campaigns.
    resolved_shots: list[ResolvedVideoShot] = field(default_factory=list)


class VideoRenderer(Protocol):
    name: str

    async def render(self, request: LocalRenderRequest) -> RenderedVideo: ...
