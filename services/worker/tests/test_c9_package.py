import hashlib
import io
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.artifacts import AudioArtifactReference, ImageArtifactReference, VideoArtifactReference
from campaign_contracts.campaign import (
    ApprovalRecord,
    CampaignConstraints,
    CampaignCopy,
    CampaignVersion,
    ChannelCopy,
    RetryMetadata,
    Storyboard,
    StoryboardScene,
    StrategyOutput,
)
from campaign_contracts.enums import CampaignStatus, WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.package.builder import build_package, member_names
from campaign_worker.package.models import BuiltPackage
from campaign_worker.package.pipeline import S3PackagePipeline, deterministic_package_artifact_id
from campaign_worker.storage.s3_artifact_store import S3ArtifactStore


def _strategy() -> StrategyOutput:
    return StrategyOutput(
        audience="Urban professionals",
        positioning="Example Coffee for Cold brew subscription",
        objective="increase online subscription sales",
        key_message="Fresh coffee delivered weekly.",
        channel_rationale={"instagram": "Reach Urban professionals on instagram"},
    )


def _copy() -> CampaignCopy:
    return CampaignCopy(
        headline="Example Coffee: Fresh coffee delivered weekly.",
        caption="A local roaster offering weekly cold brew delivery.",
        call_to_action="Subscribe today",
        hashtags=["#ExampleCoffee"],
        channel_variants=[
            ChannelCopy(
                channel="instagram",
                headline="Example Coffee",
                caption="Fresh",
                call_to_action="Subscribe today",
                hashtags=["#ExampleCoffee"],
            )
        ],
    )


def _storyboard() -> Storyboard:
    return Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration="Fresh coffee delivered weekly.",
                visual_prompt=f"artisan cold brew scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )


def _version(*, campaign_version: int = 2, approval: ApprovalRecord | None = None) -> CampaignVersion:
    now = datetime.now(UTC)
    brief = CampaignCreationRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        business_description="A local roaster offering weekly cold brew delivery.",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        tone="bright",
        language="en-US",
        target_audience="Urban professionals",
    )
    campaign_id = uuid4()
    image_artifacts = [
        ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/jpeg",
            size_bytes=1024,
            checksum_sha256=str(n) * 64,
            created_at=now,
            provider="pexels",
            scene_number=n,
        )
        for n in (1, 2, 3)
    ]
    voice_artifact = AudioArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        workflow_step=WorkflowStep.VOICEOVER,
        mime_type="audio/mpeg",
        size_bytes=4096,
        checksum_sha256="b" * 64,
        created_at=now,
        provider="polly",
    )
    video_artifact = VideoArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        workflow_step=WorkflowStep.VIDEO,
        mime_type="video/mp4",
        size_bytes=2_000_000,
        checksum_sha256="d" * 64,
        created_at=now,
        provider="ffmpeg",
    )
    return CampaignVersion(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        parent_version=campaign_version - 1 if campaign_version > 1 else None,
        job_id=uuid4(),
        status=CampaignStatus.APPROVED,
        progress_percent=98,
        brief=brief,
        constraints=CampaignConstraints(),
        strategy=_strategy(),
        copy=_copy(),
        storyboard=_storyboard(),
        image_artifacts=image_artifacts,
        voice_artifact=voice_artifact,
        video_artifact=video_artifact,
        approval=approval,
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


class _OutageS3:
    def put_object(self, **kwargs):
        raise RuntimeError("boom")

    def get_object(self, **kwargs):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# package/builder.py -- pure zipfile assembly (no S3/network)
# ---------------------------------------------------------------------------


def _images() -> list[tuple[int, bytes]]:
    return [(1, b"scene-1-bytes"), (2, b"scene-2-bytes"), (3, b"scene-3-bytes")]


def _build(**overrides) -> BuiltPackage:
    kwargs = dict(
        campaign_version=2,
        strategy=_strategy(),
        campaign_copy=_copy(),
        storyboard=_storyboard(),
        images=_images(),
        audio=b"audio-bytes",
        video=b"video-bytes",
        manifest={"campaign_id": "abc"},
        max_size_bytes=10_000_000,
    )
    kwargs.update(overrides)
    return build_package(**kwargs)


def test_builder_produces_exactly_the_expected_member_names():
    built = _build()
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        names = set(archive.namelist())
    assert names == {
        "campaign-v2/strategy.json",
        "campaign-v2/copy.json",
        "campaign-v2/storyboard.json",
        "campaign-v2/images/scene-1.jpg",
        "campaign-v2/images/scene-2.jpg",
        "campaign-v2/images/scene-3.jpg",
        "campaign-v2/audio/voiceover.mp3",
        "campaign-v2/video/final.mp4",
        "campaign-v2/manifest.json",
    }


def test_builder_member_names_never_derive_from_user_input():
    # member_names() takes only an integer campaign_version -- there is no code path
    # by which a business name, brief text, or any other user-controlled string could
    # reach an archive member path.
    names = member_names(3)
    assert names["strategy"] == "campaign-v3/strategy.json"
    assert names["images"][1] == "campaign-v3/images/scene-1.jpg"


def test_builder_embeds_raw_image_audio_and_video_bytes():
    built = _build()
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        assert archive.read("campaign-v2/images/scene-1.jpg") == b"scene-1-bytes"
        assert archive.read("campaign-v2/images/scene-2.jpg") == b"scene-2-bytes"
        assert archive.read("campaign-v2/images/scene-3.jpg") == b"scene-3-bytes"
        assert archive.read("campaign-v2/audio/voiceover.mp3") == b"audio-bytes"
        assert archive.read("campaign-v2/video/final.mp4") == b"video-bytes"


def test_builder_embeds_serialized_strategy_copy_and_storyboard_json():
    built = _build()
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        import json

        strategy_payload = json.loads(archive.read("campaign-v2/strategy.json"))
        assert strategy_payload["key_message"] == "Fresh coffee delivered weekly."
        copy_payload = json.loads(archive.read("campaign-v2/copy.json"))
        assert copy_payload["headline"] == "Example Coffee: Fresh coffee delivered weekly."
        storyboard_payload = json.loads(archive.read("campaign-v2/storyboard.json"))
        assert len(storyboard_payload["scenes"]) == 3


def test_builder_embeds_the_manifest_payload_verbatim():
    built = _build(manifest={"campaign_id": "abc", "title": "Example Coffee"})
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        import json

        manifest_payload = json.loads(archive.read("campaign-v2/manifest.json"))
    assert manifest_payload == {"campaign_id": "abc", "title": "Example Coffee"}


def test_builder_checksum_matches_the_returned_bytes():
    built = _build()
    assert built.checksum_sha256 == hashlib.sha256(built.data).hexdigest()
    assert built.size_bytes == len(built.data)


def test_builder_is_deterministic_for_identical_inputs():
    first = _build()
    second = _build()
    assert first.data == second.data
    assert first.checksum_sha256 == second.checksum_sha256


def test_builder_rejects_missing_or_mislabeled_scenes():
    with pytest.raises(ValueError, match="scene"):
        _build(images=[(1, b"a"), (2, b"b")])
    with pytest.raises(ValueError, match="scene"):
        _build(images=[(1, b"a"), (2, b"b"), (4, b"d")])


def test_builder_enforces_the_configured_size_limit():
    with pytest.raises(WorkflowOperationError) as error:
        _build(max_size_bytes=10)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"
    assert error.value.retryable is False


# ---------------------------------------------------------------------------
# storage/s3_artifact_store.py -- package put/reconcile
# ---------------------------------------------------------------------------


def test_deterministic_package_artifact_id_is_stable_per_campaign_version():
    campaign_id = uuid4()
    first = deterministic_package_artifact_id(campaign_id, 2)
    second = deterministic_package_artifact_id(campaign_id, 2)
    third = deterministic_package_artifact_id(campaign_id, 3)
    assert first == second
    assert first != third


def _built_package(payload: bytes = b"zip-bytes") -> BuiltPackage:
    return BuiltPackage(data=payload, checksum_sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))


def test_s3_package_put_and_reconcile_round_trip():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    package = _built_package()
    fingerprint = "a" * 64
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": version.campaign_version,
        "package_fingerprint": fingerprint,
        "checksum_sha256": package.checksum_sha256,
        "size_bytes": package.size_bytes,
    }
    stored = store.put_package(version, package, metadata)
    assert stored.artifact_id == deterministic_package_artifact_id(version.campaign_id, version.campaign_version)
    assert f"campaigns/{version.campaign_id}/versions/2/package/campaign.zip" in store._client.objects
    assert store.reconcile_package(version, fingerprint) == stored


def test_s3_package_reconcile_returns_none_when_object_absent():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    assert store.reconcile_package(version, "any-fingerprint") is None


def test_s3_package_reconcile_returns_none_on_fingerprint_mismatch():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    package = _built_package()
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": version.campaign_version,
        "package_fingerprint": "stale-fingerprint",
        "checksum_sha256": package.checksum_sha256,
    }
    store.put_package(version, package, metadata)
    assert store.reconcile_package(version, "new-fingerprint") is None


def test_s3_package_reconciliation_outage_is_not_treated_as_a_cache_miss():
    version = _version()
    store = S3ArtifactStore(_OutageS3(), "private-bucket")
    with pytest.raises(WorkflowOperationError) as error:
        store.reconcile_package(version, "fingerprint")
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_package_put_failure_maps_to_storage_unavailable():
    version = _version()
    store = S3ArtifactStore(_OutageS3(), "private-bucket")
    package = _built_package()
    with pytest.raises(WorkflowOperationError) as error:
        store.put_package(version, package, {"campaign_id": str(version.campaign_id)})
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_package_reconcile_non_404_client_error_maps_to_storage_unavailable():
    class _DeniedS3:
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    version = _version()
    store = S3ArtifactStore(_DeniedS3(), "private-bucket")
    with pytest.raises(WorkflowOperationError) as error:
        store.reconcile_package(version, "fingerprint")
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_s3_package_reconcile_does_not_trust_corrupt_metadata():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    prefix = f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/package/campaign"
    s3.objects[f"{prefix}.zip"] = b"zip-bytes"
    s3.objects[f"{prefix}.metadata.json"] = b"{not-valid-json"
    assert store.reconcile_package(version, "any-fingerprint") is None


# ---------------------------------------------------------------------------
# package/pipeline.py -- S3PackagePipeline orchestration
# ---------------------------------------------------------------------------


def _image_key(artifact) -> str:
    prefix = f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}"
    return f"{prefix}/images/scene-{artifact.scene_number}.jpg"


def _audio_key(artifact) -> str:
    return f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}/audio/voiceover.mp3"


def _video_key(artifact) -> str:
    return f"campaigns/{artifact.campaign_id}/versions/{artifact.campaign_version}/video/final.mp4"


def _populated_s3(version: CampaignVersion) -> _S3:
    s3 = _S3()
    for artifact in version.image_artifacts:
        s3.objects[_image_key(artifact)] = f"jpeg-{artifact.scene_number}".encode()
    s3.objects[_audio_key(version.voice_artifact)] = b"fake-mp3-bytes"
    s3.objects[_video_key(version.video_artifact)] = b"fake-mp4-bytes"
    return s3


async def _never_cancelled() -> bool:
    return False


def _pipeline(s3=None, store=None, **overrides) -> S3PackagePipeline:
    s3 = s3 or _S3()
    return S3PackagePipeline(
        s3,
        store or S3ArtifactStore(s3, "private-bucket"),
        "private-bucket",
        **overrides,
    )


@pytest.mark.asyncio
async def test_pipeline_builds_and_stores_a_new_package():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    artifact = await pipeline.acquire(version, _never_cancelled)

    assert artifact.workflow_step == WorkflowStep.PACKAGE
    assert artifact.mime_type == "application/zip"
    assert artifact.artifact_id == deterministic_package_artifact_id(version.campaign_id, version.campaign_version)
    assert f"campaigns/{version.campaign_id}/versions/2/package/campaign.zip" in s3.objects


@pytest.mark.asyncio
async def test_pipeline_requires_strategy_copy_and_storyboard():
    version = _version().model_copy(update={"strategy": None})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="strategy"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_requires_exactly_three_image_artifacts():
    version = _version()
    version = version.model_copy(update={"image_artifacts": version.image_artifacts[:2]})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="image"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_requires_voice_artifact():
    version = _version().model_copy(update={"voice_artifact": None})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="voice"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_requires_video_artifact():
    version = _version().model_copy(update={"video_artifact": None})
    pipeline = _pipeline()
    with pytest.raises(ValueError, match="video"):
        await pipeline.acquire(version, _never_cancelled)


@pytest.mark.asyncio
async def test_pipeline_reconciles_existing_package_and_skips_rebuild():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    first = await pipeline.acquire(version, _never_cancelled)
    zip_key = f"campaigns/{version.campaign_id}/versions/2/package/campaign.zip"
    first_bytes = s3.objects[zip_key]

    second = await pipeline.acquire(version, _never_cancelled)

    assert second.artifact_id == first.artifact_id
    assert second.checksum_sha256 == first.checksum_sha256
    assert s3.objects[zip_key] == first_bytes


@pytest.mark.asyncio
async def test_pipeline_rebuilds_when_a_constituent_artifact_changes():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    first = await pipeline.acquire(version, _never_cancelled)

    changed_video = version.video_artifact.model_copy(update={"checksum_sha256": "e" * 64})
    changed_version = version.model_copy(update={"video_artifact": changed_video})
    s3.objects[_video_key(changed_video)] = b"different-video-bytes"

    second = await pipeline.acquire(changed_version, _never_cancelled)

    assert second.checksum_sha256 != first.checksum_sha256


@pytest.mark.asyncio
async def test_pipeline_fingerprint_changes_when_approval_changes():
    from campaign_contracts.campaign import ApprovalRecord

    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)
    first = await pipeline.acquire(version, _never_cancelled)

    approved = version.model_copy(
        update={
            "approval": ApprovalRecord(
                approval_id=uuid4(),
                campaign_id=version.campaign_id,
                campaign_version=version.campaign_version,
                approved_at=datetime.now(UTC),
                manifest_checksum="f" * 64,
                created_at=datetime.now(UTC),
            )
        }
    )
    second = await pipeline.acquire(approved, _never_cancelled)

    assert second.checksum_sha256 != first.checksum_sha256


@pytest.mark.asyncio
async def test_pipeline_rejects_empty_downloaded_input():
    version = _version()
    s3 = _populated_s3(version)
    s3.objects[_image_key(version.image_artifacts[0])] = b""
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_pipeline_rejects_oversized_downloaded_input():
    version = _version()
    s3 = _populated_s3(version)
    s3.objects[_image_key(version.image_artifacts[0])] = b"x" * 1000
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store, max_download_bytes=10)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_pipeline_enforces_configured_max_package_bytes():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store, max_package_bytes=10)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_pipeline_missing_s3_input_maps_to_storage_unavailable():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")  # empty -- nothing uploaded
    pipeline = _pipeline(_S3(), store)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(version, _never_cancelled)
    assert error.value.code == "STORAGE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_download():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    async def cancel_first():
        return True

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_first)
    assert "before_download" in str(error.value)


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_zip_assembly():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)
    calls = {"n": 0}

    async def cancel_before_assembly():
        calls["n"] += 1
        # 1=before_download, then one per download (3 images + audio + video = 5)
        return calls["n"] == 7

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_before_assembly)
    assert "before_zip_assembly" in str(error.value)


@pytest.mark.asyncio
async def test_pipeline_cancellation_before_s3_upload():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)
    calls = {"n": 0}

    async def cancel_before_upload():
        calls["n"] += 1
        return calls["n"] == 8

    with pytest.raises(NodeCancelled) as error:
        await pipeline.acquire(version, cancel_before_upload)
    assert "before_s3_upload" in str(error.value)
    assert f"campaigns/{version.campaign_id}/versions/2/package/campaign.zip" not in s3.objects


@pytest.mark.asyncio
async def test_pipeline_manifest_excludes_bucket_key_and_credentials():
    version = _version()
    s3 = _populated_s3(version)
    store = S3ArtifactStore(s3, "private-bucket")
    pipeline = _pipeline(s3, store)

    await pipeline.acquire(version, _never_cancelled)

    zip_key = f"campaigns/{version.campaign_id}/versions/2/package/campaign.zip"
    with zipfile.ZipFile(io.BytesIO(s3.objects[zip_key])) as archive:
        import json

        manifest_payload = json.loads(archive.read("campaign-v2/manifest.json"))
    serialized = json.dumps(manifest_payload).lower()
    for banned in ("private-bucket", "aws_access_key", "aws_secret", "s3://", "credential"):
        assert banned not in serialized
