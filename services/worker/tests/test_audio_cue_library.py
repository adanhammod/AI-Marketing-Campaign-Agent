import hashlib
import logging

import pytest
from campaign_contracts.enums import AudioCueType

from campaign_worker.video.audio_cue_library import (
    ResolvedSfxAsset,
    build_sfx_cues,
    resolve_sfx_asset,
)
from campaign_worker.video.models import ResolvedVideoShot


def _write_cue_files(root, cue_types: list[AudioCueType]) -> None:
    from campaign_worker.video.audio_cue_library import _CUE_FILENAMES

    for cue_type in cue_types:
        (root / _CUE_FILENAMES[cue_type]).write_bytes(f"fake-{cue_type.value}".encode())


# ---------------------------------------------------------------------------
# resolve_sfx_asset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cue_type", list(AudioCueType))
def test_every_audio_cue_type_resolves_to_its_configured_asset(tmp_path, cue_type):
    _write_cue_files(tmp_path, list(AudioCueType))
    resolved = resolve_sfx_asset(cue_type, library_root=tmp_path)
    assert isinstance(resolved, ResolvedSfxAsset)
    assert resolved.cue_type == cue_type
    assert resolved.path.parent == tmp_path
    assert resolved.checksum_sha256 == hashlib.sha256(f"fake-{cue_type.value}".encode()).hexdigest()


def test_resolve_sfx_asset_returns_none_when_file_missing(tmp_path):
    # Library root exists but is empty -- no file for this cue type.
    assert resolve_sfx_asset(AudioCueType.WHOOSH, library_root=tmp_path) is None


def test_resolve_sfx_asset_returns_none_when_library_root_missing(tmp_path):
    missing_root = tmp_path / "does-not-exist"
    assert resolve_sfx_asset(AudioCueType.WHOOSH, library_root=missing_root) is None


def test_resolve_sfx_asset_missing_file_logs_structured_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="campaign_worker.video.audio_cue_library"):
        resolve_sfx_asset(AudioCueType.ICE_CLINK, library_root=tmp_path)
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------------
# build_sfx_cues
# ---------------------------------------------------------------------------


def _shot(
    shot_number: int,
    duration: float,
    audio_cues: tuple[AudioCueType, ...] = (),
) -> ResolvedVideoShot:
    return ResolvedVideoShot(
        shot_number=shot_number,
        scene_number=1,
        duration_seconds=duration,
        text=None,
        camera_motion_key="static",
        transition_key="cut",
        audio_cues=audio_cues,
    )


def test_build_sfx_cues_resolves_correct_cumulative_start_times(tmp_path):
    _write_cue_files(tmp_path, list(AudioCueType))
    shots = [
        _shot(1, 3.0),
        _shot(2, 4.0, (AudioCueType.TRANSITION_HIT,)),
        _shot(3, 2.0, (AudioCueType.BRAND_HIT,)),
    ]
    cues = build_sfx_cues(shots, library_root=tmp_path)

    assert len(cues) == 2
    assert cues[0].start_seconds == pytest.approx(3.0)
    assert cues[1].start_seconds == pytest.approx(7.0)


def test_build_sfx_cues_preserves_ordering_across_multiple_cues_on_one_shot(tmp_path):
    _write_cue_files(tmp_path, list(AudioCueType))
    shots = [_shot(1, 2.0, (AudioCueType.TRANSITION_HIT, AudioCueType.WHOOSH))]
    cues = build_sfx_cues(shots, library_root=tmp_path)

    assert len(cues) == 2
    assert all(cue.start_seconds == pytest.approx(0.0) for cue in cues)


def test_build_sfx_cues_skips_unresolvable_cues_without_failing(tmp_path, caplog):
    # Empty library root -- every cue is unresolvable.
    shots = [_shot(1, 2.0, (AudioCueType.TRANSITION_HIT,)), _shot(2, 3.0, (AudioCueType.BRAND_HIT,))]
    with caplog.at_level(logging.WARNING, logger="campaign_worker.video.audio_cue_library"):
        cues = build_sfx_cues(shots, library_root=tmp_path)

    assert cues == []
    assert len(caplog.records) == 2


def test_build_sfx_cues_returns_empty_list_for_shots_with_no_cues(tmp_path):
    _write_cue_files(tmp_path, list(AudioCueType))
    shots = [_shot(1, 5.0), _shot(2, 5.0)]
    assert build_sfx_cues(shots, library_root=tmp_path) == []
