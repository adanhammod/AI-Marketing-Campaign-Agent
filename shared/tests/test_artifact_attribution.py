from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.artifacts import ArtifactAttribution, ImageArtifactReference
from campaign_contracts.enums import WorkflowStep
from pydantic import ValidationError


def _artifact(**overrides):
    values = {
        "artifact_id": uuid4(),
        "campaign_id": uuid4(),
        "campaign_version": 1,
        "workflow_step": WorkflowStep.IMAGES,
        "mime_type": "image/jpeg",
        "size_bytes": 42,
        "checksum_sha256": "a" * 64,
        "created_at": datetime.now(UTC),
        "provider": "pexels",
    }
    values.update(overrides)
    return ImageArtifactReference(**values)


def _attribution(**overrides):
    values = {
        "provider_asset_id": "123",
        "creator_name": "Creator",
        "creator_profile_url": "https://www.pexels.com/@creator",
        "source_page_url": "https://www.pexels.com/photo/example-123/",
        "provider_url": "https://www.pexels.com",
        "attribution_text": "Photo by Creator on Pexels",
    }
    values.update(overrides)
    return ArtifactAttribution(**values)


def test_public_image_supports_optional_attribution_and_scene_number():
    legacy = _artifact()
    assert legacy.scene_number is None and legacy.attribution is None
    artifact = _artifact(scene_number=1, attribution=_attribution())
    payload = artifact.model_dump(mode="json")
    assert payload["scene_number"] == 1
    assert payload["attribution"]["provider_asset_id"] == "123"
    assert "s3_bucket" not in payload and "s3_key" not in payload


@pytest.mark.parametrize("field", ["creator_profile_url", "source_page_url", "provider_url"])
def test_attribution_requires_https(field):
    with pytest.raises(ValidationError):
        _attribution(**{field: "http://www.pexels.com/unsafe"})


def test_image_scene_number_must_be_one_through_three_when_present():
    for value in (0, 4):
        with pytest.raises(ValidationError):
            _artifact(scene_number=value)


def test_ephemeral_download_fields_are_optional_and_https_only():
    legacy = _artifact()
    assert legacy.download_url is None and legacy.download_url_expires_at is None
    with pytest.raises(ValidationError):
        _artifact(download_url="http://example.test/image.jpg")
