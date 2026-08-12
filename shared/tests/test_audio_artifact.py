from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.artifacts import AudioArtifactReference
from campaign_contracts.campaign import CampaignVersion, assert_version_immutable
from campaign_contracts.enums import ArtifactType, CampaignStatus, WorkflowStep
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / "valid" / name).read_text(encoding="utf-8-sig"))


def _audio_artifact(**overrides):
    values = {
        "artifact_id": uuid4(),
        "campaign_id": uuid4(),
        "campaign_version": 1,
        "workflow_step": WorkflowStep.VOICEOVER,
        "mime_type": "audio/mpeg",
        "size_bytes": 4096,
        "checksum_sha256": "a" * 64,
        "created_at": datetime.now(UTC),
        "provider": "polly",
    }
    values.update(overrides)
    return AudioArtifactReference(**values)


def test_workflow_step_voiceover_exists_between_images_and_video():
    members = list(WorkflowStep)
    assert WorkflowStep.VOICEOVER in members
    assert members.index(WorkflowStep.IMAGES) < members.index(WorkflowStep.VOICEOVER) < members.index(
        WorkflowStep.VIDEO
    )


def test_audio_artifact_type_defaults_to_audio():
    artifact = _audio_artifact()
    assert artifact.artifact_type == ArtifactType.AUDIO


def test_audio_artifact_serialization_omits_internal_fields_and_has_no_attribution():
    artifact = _audio_artifact()
    payload = artifact.model_dump(mode="json")
    assert "s3_bucket" not in payload and "s3_key" not in payload
    assert payload["attribution"] is None
    assert payload["scene_number"] is None


def test_campaign_version_voice_artifact_defaults_to_none_for_backward_compatibility():
    version = CampaignVersion.model_validate(load("queued-campaign.json"))
    assert version.voice_artifact is None


def test_campaign_version_accepts_voice_artifact():
    version = CampaignVersion.model_validate(load("queued-campaign.json"))
    updated = version.model_copy(update={"voice_artifact": _audio_artifact(campaign_id=version.campaign_id)})
    assert updated.voice_artifact is not None
    assert updated.voice_artifact.artifact_type == ArtifactType.AUDIO


def test_assert_version_immutable_rejects_voice_artifact_change_when_ready_for_review():
    version = CampaignVersion.model_validate(load("ready-for-review.json"))
    changed = version.model_copy(update={"voice_artifact": _audio_artifact(campaign_id=version.campaign_id)})
    try:
        assert_version_immutable(version, changed)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for voice_artifact change during READY_FOR_REVIEW")
