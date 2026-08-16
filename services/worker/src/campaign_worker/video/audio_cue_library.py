"""Maps semantic AudioCueType values (VideoShot.audio_cues) to local SFX assets.

Shared contracts carry only the semantic cue type -- no file paths; this is
the one place that resolves e.g. "TRANSITION_HIT" into an actual local asset.
A missing/unconfigured optional SFX asset is never fatal: it is logged and
skipped so the render continues with the cues that *are* available, unlike
music, which is a hard requirement for CINEMATIC_TEXT_AD (see music_resolver.py
and video/pipeline.py's early validation).
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from campaign_contracts.enums import AudioCueType

from .audio_mix import SfxCue
from .models import ResolvedVideoShot

_LOG = logging.getLogger(__name__)

# Local/dev SFX library root, kept out of the repo (no committed audio
# assets) -- a real deployment configures its own library_root; when no
# asset exists at the resolved path, cues resolve to None and are skipped.
_DEFAULT_LIBRARY_ROOT = Path(__file__).resolve().parent.parent / "assets" / "sfx"

_CUE_FILENAMES: dict[AudioCueType, str] = {
    AudioCueType.TRANSITION_HIT: "transition_hit.wav",
    AudioCueType.IMPACT: "impact.wav",
    AudioCueType.WHOOSH: "whoosh.wav",
    AudioCueType.BRAND_HIT: "brand_hit.wav",
    AudioCueType.ICE_CLINK: "ice_clink.wav",
}


@dataclass(frozen=True, slots=True)
class ResolvedSfxAsset:
    cue_type: AudioCueType
    path: Path
    checksum_sha256: str


def resolve_sfx_asset(cue_type: AudioCueType, *, library_root: Path | None = None) -> ResolvedSfxAsset | None:
    root = library_root if library_root is not None else _DEFAULT_LIBRARY_ROOT
    filename = _CUE_FILENAMES.get(cue_type)
    if filename is None:
        _LOG.warning("audio_cue.unmapped", extra={"cue_type": cue_type.value})
        return None
    path = root / filename
    if not path.is_file():
        _LOG.warning("audio_cue.asset_missing", extra={"cue_type": cue_type.value, "path": str(path)})
        return None
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return ResolvedSfxAsset(cue_type=cue_type, path=path, checksum_sha256=checksum)


def build_sfx_cues(shots: list[ResolvedVideoShot], *, library_root: Path | None = None) -> list[SfxCue]:
    cues: list[SfxCue] = []
    start = 0.0
    for shot in shots:
        for cue_type in shot.audio_cues:
            resolved = resolve_sfx_asset(cue_type, library_root=library_root)
            if resolved is not None:
                cues.append(SfxCue(path=resolved.path, start_seconds=start))
        start += shot.duration_seconds
    return cues
