import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from campaign_contracts.artifacts import (
    ArtifactAttribution,
    AudioArtifactReference,
    FinalPackageArtifactReference,
    ImageArtifactReference,
    VideoArtifactReference,
)
from campaign_contracts.enums import CampaignStatus, WorkflowStep

from campaign_api.artifacts.artifact_url_signer import ArtifactURLSigner
from campaign_api.artifacts.s3_artifact_url_signer import S3ArtifactURLSigner
from campaign_api.exceptions import RepositoryFailure


class FakeSigner(ArtifactURLSigner):
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, int]] = []
        self.audio_calls: list[tuple[UUID, int]] = []
        self.video_calls: list[tuple[UUID, int]] = []
        self.package_calls: list[tuple[UUID, int]] = []

    async def sign_image(self, campaign_id: UUID, campaign_version: int, scene_number: int):
        self.calls.append((campaign_id, campaign_version, scene_number))
        return (
            f"https://downloads.example.test/{campaign_id}/{campaign_version}/{scene_number}?fresh={len(self.calls)}",
            datetime.now(UTC) + timedelta(minutes=10),
        )

    async def sign_audio(self, campaign_id: UUID, campaign_version: int):
        self.audio_calls.append((campaign_id, campaign_version))
        return (
            f"https://downloads.example.test/{campaign_id}/{campaign_version}/voiceover?fresh={len(self.audio_calls)}",
            datetime.now(UTC) + timedelta(minutes=10),
        )

    async def sign_video(self, campaign_id: UUID, campaign_version: int):
        self.video_calls.append((campaign_id, campaign_version))
        return (
            f"https://downloads.example.test/{campaign_id}/{campaign_version}/final?fresh={len(self.video_calls)}",
            datetime.now(UTC) + timedelta(minutes=10),
        )

    async def sign_package(self, campaign_id: UUID, campaign_version: int):
        self.package_calls.append((campaign_id, campaign_version))
        return (
            f"https://downloads.example.test/{campaign_id}/{campaign_version}/campaign?fresh={len(self.package_calls)}",
            datetime.now(UTC) + timedelta(minutes=10),
        )


def _images(campaign_id: UUID) -> list[ImageArtifactReference]:
    now = datetime.now(UTC)
    return [
        ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=1,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/jpeg",
            size_bytes=100 + scene,
            checksum_sha256=str(scene) * 64,
            created_at=now,
            provider="pexels",
            scene_number=scene,
            attribution=ArtifactAttribution(
                provider_asset_id=str(1000 + scene),
                creator_name=f"Photographer {scene}",
                creator_profile_url=f"https://www.pexels.com/@creator-{scene}/",
                source_page_url=f"https://www.pexels.com/photo/{1000 + scene}/",
                provider_url="https://www.pexels.com/",
                attribution_text=f"Photo by Photographer {scene} on Pexels",
            ),
        )
        for scene in range(1, 4)
    ]


def _video(campaign_id: UUID) -> VideoArtifactReference:
    return VideoArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=1,
        workflow_step=WorkflowStep.VIDEO,
        mime_type="video/mp4",
        size_bytes=200,
        checksum_sha256="f" * 64,
        created_at=datetime.now(UTC),
    )


def _package(campaign_id: UUID) -> FinalPackageArtifactReference:
    return FinalPackageArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=1,
        workflow_step=WorkflowStep.PACKAGE,
        mime_type="application/zip",
        size_bytes=500,
        checksum_sha256="e" * 64,
        created_at=datetime.now(UTC),
        provider="campaign-worker-package-builder",
    )


def _voice(campaign_id: UUID) -> AudioArtifactReference:
    return AudioArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=1,
        workflow_step=WorkflowStep.VOICEOVER,
        mime_type="audio/mpeg",
        size_bytes=4096,
        checksum_sha256="c" * 64,
        created_at=datetime.now(UTC),
        provider="polly",
    )


def test_detail_projects_three_images_and_existing_video_without_private_locations(client, app, campaign_at_status):
    actual_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    images = _images(actual_id)
    video = _video(actual_id)
    record = asyncio.run(app.state.repository.get(actual_id))
    assert record is not None
    aggregate, version = record
    asyncio.run(
        app.state.repository.replace_current(
            aggregate,
            version.model_copy(update={"image_artifacts": images, "video_artifact": video}),
        )
    )

    response = client.get(f"/api/v1/campaigns/{actual_id}")
    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert len([item for item in artifacts if item["artifact_type"] == "IMAGE"]) == 3
    assert len([item for item in artifacts if item["artifact_type"] == "VIDEO"]) == 1
    assert [item["scene_number"] for item in artifacts[:3]] == [1, 2, 3]
    assert all(item["provider"] == "pexels" for item in artifacts[:3])
    assert all(item["attribution"]["provider_asset_id"] for item in artifacts[:3])
    assert all(item["download_url"] is None for item in artifacts)
    assert "s3_bucket" not in response.text and "s3_key" not in response.text


def test_detail_projects_voice_artifact_alongside_images_and_video(client, app, campaign_at_status):
    actual_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    voice = _voice(actual_id)
    record = asyncio.run(app.state.repository.get(actual_id))
    assert record is not None
    aggregate, version = record
    asyncio.run(app.state.repository.replace_current(aggregate, version.model_copy(update={"voice_artifact": voice})))

    response = client.get(f"/api/v1/campaigns/{actual_id}")

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    audio_items = [item for item in artifacts if item["artifact_type"] == "AUDIO"]
    assert len(audio_items) == 1
    assert audio_items[0]["provider"] == "polly"
    assert audio_items[0]["attribution"] is None
    assert "s3_bucket" not in response.text and "s3_key" not in response.text


def test_artifacts_endpoint_signs_fresh_audio_download_url(client, app, campaign_at_status):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    voice = _voice(campaign_id)
    asyncio.run(app.state.repository.replace_current(aggregate, version.model_copy(update={"voice_artifact": voice})))
    signer = FakeSigner()
    app.state.artifact_signer = signer

    response = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts?type=AUDIO")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["artifact_id"] == str(voice.artifact_id)
    assert items[0]["download_url"].startswith("https://")
    assert signer.audio_calls == [(campaign_id, 1)]
    persisted = asyncio.run(app.state.repository.get(campaign_id))
    assert persisted is not None
    assert persisted[1].voice_artifact.download_url is None
    assert "s3_bucket" not in response.text and "s3_key" not in response.text


def test_artifacts_endpoint_signs_fresh_video_download_url(client, app, campaign_at_status):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    video = _video(campaign_id)
    asyncio.run(app.state.repository.replace_current(aggregate, version.model_copy(update={"video_artifact": video})))
    signer = FakeSigner()
    app.state.artifact_signer = signer

    response = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts?type=VIDEO")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["artifact_id"] == str(video.artifact_id)
    assert items[0]["download_url"].startswith("https://")
    assert signer.video_calls == [(campaign_id, 1)]
    persisted = asyncio.run(app.state.repository.get(campaign_id))
    assert persisted is not None
    assert persisted[1].video_artifact.download_url is None
    assert "s3_bucket" not in response.text and "s3_key" not in response.text


def test_artifacts_endpoint_signs_fresh_package_download_url(client, app, campaign_at_status):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FINAL))
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    package = _package(campaign_id)
    asyncio.run(
        app.state.repository.replace_current(aggregate, version.model_copy(update={"package_artifact": package}))
    )
    signer = FakeSigner()
    app.state.artifact_signer = signer

    response = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts?type=FINAL_PACKAGE")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["artifact_id"] == str(package.artifact_id)
    assert items[0]["download_url"].startswith("https://")
    assert signer.package_calls == [(campaign_id, 1)]
    persisted = asyncio.run(app.state.repository.get(campaign_id))
    assert persisted is not None
    assert persisted[1].package_artifact.download_url is None
    assert "s3_bucket" not in response.text and "s3_key" not in response.text


def test_detail_projects_final_package_artifact(client, app, campaign_at_status):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FINAL))
    package = _package(campaign_id)
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    asyncio.run(
        app.state.repository.replace_current(aggregate, version.model_copy(update={"package_artifact": package}))
    )

    response = client.get(f"/api/v1/campaigns/{campaign_id}")

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    package_items = [item for item in artifacts if item["artifact_type"] == "FINAL_PACKAGE"]
    assert len(package_items) == 1
    assert package_items[0]["artifact_id"] == str(package.artifact_id)


def test_artifacts_endpoint_signs_fresh_urls_and_filters_images(client, app, campaign_at_status):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    images = _images(campaign_id)
    video = _video(campaign_id)
    asyncio.run(
        app.state.repository.replace_current(
            aggregate,
            version.model_copy(update={"image_artifacts": images, "video_artifact": video}),
        )
    )
    signer = FakeSigner()
    app.state.artifact_signer = signer

    first = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts?type=IMAGE")
    second = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts?version=1&type=IMAGE")
    assert first.status_code == second.status_code == 200
    assert len(first.json()["items"]) == 3
    assert [item["artifact_id"] for item in first.json()["items"]] == [str(image.artifact_id) for image in images]
    assert all(item["download_url"].startswith("https://") for item in first.json()["items"])
    assert first.json()["items"][0]["download_url"] != second.json()["items"][0]["download_url"]
    assert [call[2] for call in signer.calls] == [1, 2, 3, 1, 2, 3]
    persisted = asyncio.run(app.state.repository.get(campaign_id))
    assert persisted is not None
    assert all(image.download_url is None for image in persisted[1].image_artifacts)
    assert "s3_bucket" not in first.text and "s3_key" not in first.text


def test_artifacts_endpoint_returns_all_four_artifact_types_together_for_a_completed_campaign(
    client, app, campaign_at_status
):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.FINAL))
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    images = _images(campaign_id)
    voice = _voice(campaign_id)
    video = _video(campaign_id)
    package = _package(campaign_id)
    asyncio.run(
        app.state.repository.replace_current(
            aggregate,
            version.model_copy(
                update={
                    "image_artifacts": images,
                    "voice_artifact": voice,
                    "video_artifact": video,
                    "package_artifact": package,
                }
            ),
        )
    )
    app.state.artifact_signer = FakeSigner()

    response = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts")

    assert response.status_code == 200
    items = response.json()["items"]
    by_type: dict[str, list] = {}
    for item in items:
        by_type.setdefault(item["artifact_type"], []).append(item)
    assert len(by_type["IMAGE"]) == 3
    assert len(by_type["AUDIO"]) == 1
    assert len(by_type["VIDEO"]) == 1
    assert len(by_type["FINAL_PACKAGE"]) == 1
    assert all(item["download_url"].startswith("https://") for item in items)
    assert "s3_bucket" not in response.text and "s3_key" not in response.text


def test_legacy_image_without_scene_or_attribution_remains_compatible(client, app, campaign_at_status):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    legacy = ImageArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=1,
        workflow_step=WorkflowStep.IMAGES,
        mime_type="image/jpeg",
        size_bytes=1,
        checksum_sha256="a" * 64,
        created_at=datetime.now(UTC),
        provider="pexels",
    )
    asyncio.run(
        app.state.repository.replace_current(
            aggregate,
            version.model_copy(update={"image_artifacts": [legacy]}),
        )
    )
    signer = FakeSigner()
    app.state.artifact_signer = signer

    response = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["scene_number"] is None
    assert item["attribution"] is None
    assert item["download_url"] is None
    assert signer.calls == []


def test_missing_campaign_version_is_not_found(client, campaign_at_status):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    response = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts?version=2")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_signer_failure_is_sanitized(client, app, campaign_at_status):
    class FailingSigner(ArtifactURLSigner):
        async def sign_image(self, campaign_id: UUID, campaign_version: int, scene_number: int):
            raise RepositoryFailure("secret-bucket AWS authorization failure")

        async def sign_audio(self, campaign_id: UUID, campaign_version: int):
            raise RepositoryFailure("secret-bucket AWS authorization failure")

        async def sign_video(self, campaign_id: UUID, campaign_version: int):
            raise RepositoryFailure("secret-bucket AWS authorization failure")

        async def sign_package(self, campaign_id: UUID, campaign_version: int):
            raise RepositoryFailure("secret-bucket AWS authorization failure")

    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    record = asyncio.run(app.state.repository.get(campaign_id))
    assert record is not None
    aggregate, version = record
    asyncio.run(
        app.state.repository.replace_current(
            aggregate,
            version.model_copy(update={"image_artifacts": _images(campaign_id)}),
        )
    )
    app.state.artifact_signer = FailingSigner()

    response = client.get(f"/api/v1/campaigns/{campaign_id}/artifacts")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert "secret-bucket" not in response.text
    assert "authorization" not in response.text.lower()


def test_s3_signer_uses_validated_identity_and_short_expiration():
    class Client:
        def __init__(self) -> None:
            self.call = None

        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            self.call = (operation, Params, ExpiresIn)
            return "https://signed.example.test/object"

    client = Client()
    signer = S3ArtifactURLSigner(client, "private-artifacts", 900)
    campaign_id = uuid4()
    before = datetime.now(UTC)
    url, expires_at = asyncio.run(signer.sign_image(campaign_id, 2, 3))

    assert url.startswith("https://")
    assert client.call == (
        "get_object",
        {
            "Bucket": "private-artifacts",
            "Key": f"campaigns/{campaign_id}/versions/2/images/scene-3.jpg",
        },
        900,
    )
    assert before + timedelta(seconds=895) <= expires_at <= datetime.now(UTC) + timedelta(seconds=900)


def test_s3_signer_signs_the_deterministic_video_key():
    class Client:
        def __init__(self) -> None:
            self.call = None

        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            self.call = (operation, Params, ExpiresIn)
            return "https://signed.example.test/final"

    client = Client()
    signer = S3ArtifactURLSigner(client, "private-artifacts", 900)
    campaign_id = uuid4()
    before = datetime.now(UTC)
    url, expires_at = asyncio.run(signer.sign_video(campaign_id, 2))

    assert url.startswith("https://")
    assert client.call == (
        "get_object",
        {
            "Bucket": "private-artifacts",
            "Key": f"campaigns/{campaign_id}/versions/2/video/final.mp4",
        },
        900,
    )
    assert before + timedelta(seconds=895) <= expires_at <= datetime.now(UTC) + timedelta(seconds=900)


def test_s3_signer_signs_the_deterministic_package_key():
    class Client:
        def __init__(self) -> None:
            self.call = None

        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            self.call = (operation, Params, ExpiresIn)
            return "https://signed.example.test/campaign"

    client = Client()
    signer = S3ArtifactURLSigner(client, "private-artifacts", 900)
    campaign_id = uuid4()
    before = datetime.now(UTC)
    url, expires_at = asyncio.run(signer.sign_package(campaign_id, 2))

    assert url.startswith("https://")
    assert client.call == (
        "get_object",
        {
            "Bucket": "private-artifacts",
            "Key": f"campaigns/{campaign_id}/versions/2/package/campaign.zip",
        },
        900,
    )
    assert before + timedelta(seconds=895) <= expires_at <= datetime.now(UTC) + timedelta(seconds=900)


def test_s3_signer_signs_the_deterministic_audio_key():
    class Client:
        def __init__(self) -> None:
            self.call = None

        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            self.call = (operation, Params, ExpiresIn)
            return "https://signed.example.test/voiceover"

    client = Client()
    signer = S3ArtifactURLSigner(client, "private-artifacts", 900)
    campaign_id = uuid4()
    before = datetime.now(UTC)
    url, expires_at = asyncio.run(signer.sign_audio(campaign_id, 2))

    assert url.startswith("https://")
    assert client.call == (
        "get_object",
        {
            "Bucket": "private-artifacts",
            "Key": f"campaigns/{campaign_id}/versions/2/audio/voiceover.mp3",
        },
        900,
    )
    assert before + timedelta(seconds=895) <= expires_at <= datetime.now(UTC) + timedelta(seconds=900)
