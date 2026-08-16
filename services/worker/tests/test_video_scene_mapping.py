from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.artifacts import ImageArtifactReference
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.video.scene_mapping import resolve_scene_artifacts


def _artifact(scene_number, checksum=None, **overrides):
    checksum = checksum or (str(scene_number) * 64 if scene_number is not None else "9" * 64)
    defaults = dict(
        artifact_id=uuid4(),
        campaign_id=uuid4(),
        campaign_version=1,
        workflow_step=WorkflowStep.IMAGES,
        mime_type="image/jpeg",
        size_bytes=1024,
        checksum_sha256=checksum,
        created_at=datetime.now(UTC),
        provider="stability",
        scene_number=scene_number,
    )
    defaults.update(overrides)
    return ImageArtifactReference(**defaults)


def test_resolves_three_scenes_by_scene_number_regardless_of_list_order():
    a1, a2, a3 = _artifact(1), _artifact(2), _artifact(3)
    resolved = resolve_scene_artifacts([a3, a1, a2])
    assert resolved == {1: a1, 2: a2, 3: a3}


def test_raises_when_a_scene_number_is_missing():
    artifacts = [_artifact(1), _artifact(2)]
    with pytest.raises(WorkflowOperationError) as error:
        resolve_scene_artifacts(artifacts)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert "3" in str(error.value)


def test_raises_on_duplicate_scene_number():
    artifacts = [_artifact(1), _artifact(1, checksum="a" * 64), _artifact(2), _artifact(3)]
    with pytest.raises(WorkflowOperationError) as error:
        resolve_scene_artifacts(artifacts)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert "duplicate" in str(error.value).lower()


def test_raises_when_two_scenes_resolve_to_the_identical_image():
    # Regression guard for the real production bug: the rendered video
    # appeared to reuse the same visual throughout because two different
    # scenes ended up pointing at the same generated image.
    shared_checksum = "b" * 64
    artifacts = [
        _artifact(1, checksum=shared_checksum),
        _artifact(2, checksum=shared_checksum),
        _artifact(3),
    ]
    with pytest.raises(WorkflowOperationError) as error:
        resolve_scene_artifacts(artifacts)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert "identical" in str(error.value).lower()


def test_raises_when_an_artifact_is_missing_scene_number():
    artifacts = [_artifact(1), _artifact(2), _artifact(None)]
    with pytest.raises(WorkflowOperationError) as error:
        resolve_scene_artifacts(artifacts)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
