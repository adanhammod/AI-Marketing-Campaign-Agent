import io
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.artifacts import ArtifactAttribution, ImageArtifactReference
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, Storyboard, StoryboardScene
from campaign_contracts.enums import CampaignStatus, WorkflowStep
from PIL import Image

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.images.generative_pipeline import GenerativeImagePipeline, generative_scene_fingerprint
from campaign_worker.images.generative_prompt import build_generative_prompt
from campaign_worker.images.pipeline import deterministic_artifact_id
from campaign_worker.images.processor import ImageProcessor
from campaign_worker.providers.stability_client import GeneratedImage
from campaign_worker.storage.artifact_store import StoredImage

_SCENE_PATTERN = re.compile(r"Scene (\d) of 3")


def _version() -> CampaignVersion:
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
    storyboard = Storyboard(
        scenes=[
            StoryboardScene(
                scene_number=n,
                purpose=f"Scene {n}",
                duration_seconds=5,
                narration="Fresh coffee delivered",
                visual_prompt=f"artisan cold brew scene {n}",
                transition="cut",
            )
            for n in (1, 2, 3)
        ],
        total_duration_seconds=15,
    )
    return CampaignVersion(
        campaign_id=uuid4(),
        campaign_version=2,
        parent_version=1,
        job_id=uuid4(),
        status=CampaignStatus.QUEUED,
        progress_percent=2,
        brief=brief,
        constraints=CampaignConstraints(),
        storyboard=storyboard,
        retry=RetryMetadata(),
        created_at=now,
        updated_at=now,
        lock_version=1,
    )


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1200, 2000), "blue").save(output, format="JPEG")
    return output.getvalue()


async def _false() -> bool:
    return False


async def _true() -> bool:
    return True


class _Stability:
    def __init__(self, fail_scenes: set[int] | None = None, error: WorkflowOperationError | None = None) -> None:
        self.scene_calls: list[int] = []
        self._fail_scenes = fail_scenes or set()
        self._error = error or WorkflowOperationError("PROVIDER_TIMEOUT", "stability timed out", retryable=True)

    async def generate(self, positive_prompt: str, negative_prompt: str) -> GeneratedImage:
        match = _SCENE_PATTERN.search(positive_prompt)
        assert match is not None
        scene_number = int(match.group(1))
        self.scene_calls.append(scene_number)
        if scene_number in self._fail_scenes:
            raise self._error
        return GeneratedImage(
            data=_jpeg(), content_type="image/jpeg", seed=1000 + scene_number, finish_reason="SUCCESS"
        )


class _Store:
    def __init__(self) -> None:
        self.saved: dict[int, StoredImage] = {}
        self.metadata: dict[int, dict[str, object]] = {}

    def reconcile(self, version: CampaignVersion, scene_number: int, fingerprint: str) -> StoredImage | None:
        stored = self.saved.get(scene_number)
        if stored is None:
            return None
        if self.metadata[scene_number].get("storyboard_fingerprint") != fingerprint:
            return None
        return stored

    def put(self, version, scene_number, image, metadata) -> StoredImage:
        pexels_photo_id = metadata.get("pexels_photo_id")
        attribution: ArtifactAttribution | None = None
        if pexels_photo_id is not None:
            attribution = ArtifactAttribution(
                provider_asset_id=str(pexels_photo_id),
                creator_name="Creator",
                creator_profile_url="https://www.pexels.com/@creator",
                source_page_url="https://www.pexels.com/photo/example/",
                provider_url="https://www.pexels.com",
                attribution_text="Photo by Creator on Pexels",
            )
        stored = StoredImage(
            deterministic_artifact_id(version.campaign_id, version.campaign_version, scene_number),
            image.checksum_sha256,
            len(image.data),
            datetime.now(UTC),
            int(pexels_photo_id) if pexels_photo_id is not None else None,
            attribution,
        )
        self.saved[scene_number] = stored
        self.metadata[scene_number] = metadata
        return stored


class _FakeStockFallback:
    """Minimal stand-in for StockImagePipeline: writes Pexels-shaped
    artifacts into the SAME shared store, to exercise the shared-S3-key
    overwrite behavior GenerativeImagePipeline relies on for its
    whole-version-fallback invariant. StockImagePipeline itself is already
    covered by test_c6_images.py.
    """

    def __init__(self, store: _Store) -> None:
        self._store = store
        self.calls = 0

    async def acquire(self, version: CampaignVersion, is_cancelled) -> list[ImageArtifactReference]:
        self.calls += 1
        image = ImageProcessor().normalize(_jpeg())
        refs = []
        for scene_number in (1, 2, 3):
            metadata: dict[str, object] = {
                "campaign_id": str(version.campaign_id),
                "campaign_version": version.campaign_version,
                "scene_number": scene_number,
                "storyboard_fingerprint": "stock-fingerprint",
                "normalized_checksum": image.checksum_sha256,
                "pexels_photo_id": 100 + scene_number,
            }
            stored = self._store.put(version, scene_number, image, metadata)
            refs.append(
                ImageArtifactReference(
                    artifact_id=stored.artifact_id,
                    campaign_id=version.campaign_id,
                    campaign_version=version.campaign_version,
                    workflow_step=WorkflowStep.IMAGES,
                    mime_type="image/jpeg",
                    size_bytes=stored.size_bytes,
                    checksum_sha256=stored.checksum_sha256,
                    scene_number=scene_number,
                    attribution=stored.attribution,
                    created_at=stored.created_at,
                    provider="pexels",
                )
            )
        return refs


class _FailingFallback:
    async def acquire(self, version: CampaignVersion, is_cancelled) -> list[ImageArtifactReference]:
        raise WorkflowOperationError("INVALID_PROVIDER_OUTPUT", "pexels also failed", retryable=False)


def _pipeline(
    stability: _Stability, store: _Store, fallback=None, model: str = "sd3.5-large"
) -> GenerativeImagePipeline:
    return GenerativeImagePipeline(
        stability,
        fallback,
        ImageProcessor(),
        store,
        model=model,
        aspect_ratio="9:16",
        output_format="jpeg",
    )


@pytest.mark.asyncio
async def test_generative_success_produces_three_normalized_stored_image_artifacts():
    stability, store = _Stability(), _Store()
    result = await _pipeline(stability, store).acquire(_version(), _false)

    assert len(result) == 3
    assert len({artifact.artifact_id for artifact in result}) == 3
    assert [artifact.scene_number for artifact in result] == [1, 2, 3]
    assert all(artifact.mime_type == "image/jpeg" for artifact in result)
    assert stability.scene_calls == [1, 2, 3]


@pytest.mark.asyncio
async def test_generative_artifacts_record_stability_provider_and_model_in_generation_summary():
    stability, store = _Stability(), _Store()
    result = await _pipeline(stability, store).acquire(_version(), _false)

    assert all(artifact.provider == "stability-ai" for artifact in result)
    assert all(artifact.attribution is None for artifact in result)
    for n in (1, 2, 3):
        assert store.metadata[n]["model"] == "sd3.5-large"
        assert store.metadata[n]["aspect_ratio"] == "9:16"
        assert store.metadata[n]["provider"] == "stability-ai"
        assert "prompt_fingerprint" in store.metadata[n]


@pytest.mark.asyncio
async def test_identical_generative_inputs_reconcile_from_cache_without_calling_stability_twice():
    version = _version()
    stability, store = _Stability(), _Store()
    pipeline = _pipeline(stability, store)

    first = await pipeline.acquire(version, _false)
    assert stability.scene_calls == [1, 2, 3]

    second = await pipeline.acquire(version, _false)
    assert second == first
    assert stability.scene_calls == [1, 2, 3]  # no new calls


@pytest.mark.asyncio
async def test_changed_scene_visual_prompt_invalidates_only_that_scenes_generative_cache():
    version = _version()
    store = _Store()
    await _pipeline(_Stability(), store).acquire(version, _false)

    scenes = list(version.storyboard.scenes)
    scenes[1] = scenes[1].model_copy(update={"visual_prompt": "a totally different scene direction"})
    modified = version.model_copy(update={"storyboard": version.storyboard.model_copy(update={"scenes": scenes})})

    stability2 = _Stability()
    await _pipeline(stability2, store).acquire(modified, _false)

    assert stability2.scene_calls == [2]


@pytest.mark.asyncio
async def test_changed_stability_model_invalidates_generative_cache_for_all_scenes():
    version = _version()
    store = _Store()
    await _pipeline(_Stability(), store, model="sd3.5-large").acquire(version, _false)

    stability2 = _Stability()
    await _pipeline(stability2, store, model="sd3.5-large-v2").acquire(version, _false)

    assert stability2.scene_calls == [1, 2, 3]


@pytest.mark.asyncio
async def test_stability_timeout_falls_back_to_stock_pipeline_and_returns_pexels_artifacts():
    store = _Store()
    stability = _Stability(
        fail_scenes={2}, error=WorkflowOperationError("PROVIDER_TIMEOUT", "timed out", retryable=True)
    )
    fallback = _FakeStockFallback(store)

    result = await _pipeline(stability, store, fallback).acquire(_version(), _false)

    assert fallback.calls == 1
    assert all(artifact.provider == "pexels" for artifact in result)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_stability_provider_unavailable_falls_back_to_stock_pipeline():
    store = _Store()
    stability = _Stability(
        fail_scenes={1}, error=WorkflowOperationError("IMAGE_PROVIDER_UNAVAILABLE", "down", retryable=True)
    )
    fallback = _FakeStockFallback(store)

    result = await _pipeline(stability, store, fallback).acquire(_version(), _false)

    assert fallback.calls == 1
    assert all(artifact.provider == "pexels" for artifact in result)


@pytest.mark.asyncio
async def test_stability_throttled_and_insufficient_credits_fall_back_to_stock_pipeline():
    for code, retryable in (("PROVIDER_THROTTLED", True), ("PROVIDER_THROTTLED", False)):
        store = _Store()
        stability = _Stability(fail_scenes={3}, error=WorkflowOperationError(code, "throttled", retryable=retryable))
        fallback = _FakeStockFallback(store)

        result = await _pipeline(stability, store, fallback).acquire(_version(), _false)

        assert fallback.calls == 1
        assert all(artifact.provider == "pexels" for artifact in result)


@pytest.mark.asyncio
async def test_stability_invalid_image_response_falls_back_to_stock_pipeline():
    store = _Store()
    stability = _Stability(
        fail_scenes={1}, error=WorkflowOperationError("INVALID_PROVIDER_OUTPUT", "bad image", retryable=False)
    )
    fallback = _FakeStockFallback(store)

    result = await _pipeline(stability, store, fallback).acquire(_version(), _false)

    assert fallback.calls == 1
    assert all(artifact.provider == "pexels" for artifact in result)


@pytest.mark.asyncio
async def test_when_fallback_pexels_also_fails_the_error_code_propagates_unmasked():
    store = _Store()
    stability = _Stability(fail_scenes={1})
    pipeline = _pipeline(stability, store, _FailingFallback())

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(_version(), _false)
    assert error.value.code == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.asyncio
async def test_storage_unavailable_during_generative_put_is_not_treated_as_fallback_eligible():
    class _FailingStore(_Store):
        def put(self, version, scene_number, image, metadata):
            raise WorkflowOperationError("STORAGE_UNAVAILABLE", "s3 outage", retryable=True)

    store = _FailingStore()
    fallback = _FakeStockFallback(store)
    pipeline = _pipeline(_Stability(), store, fallback)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(_version(), _false)
    assert error.value.code == "STORAGE_UNAVAILABLE"
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_policy_rejection_fails_the_job_without_falling_back_to_pexels():
    store = _Store()
    stability = _Stability(
        fail_scenes={1},
        error=WorkflowOperationError("PROVIDER_POLICY_REJECTION", "content filtered", retryable=False),
    )
    fallback = _FakeStockFallback(store)
    pipeline = _pipeline(stability, store, fallback)

    with pytest.raises(WorkflowOperationError) as error:
        await pipeline.acquire(_version(), _false)
    assert error.value.code == "PROVIDER_POLICY_REJECTION"
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_generative_pipeline_cancellation_checkpoints_stop_before_stability_call():
    stability, store = _Stability(), _Store()
    pipeline = _pipeline(stability, store)

    with pytest.raises(NodeCancelled, match="between_scenes"):
        await pipeline.acquire(_version(), _true)
    assert stability.scene_calls == []


@pytest.mark.asyncio
async def test_generative_and_stock_artifacts_share_identical_reference_shape_regardless_of_provider():
    store = _Store()
    generative_result = await _pipeline(_Stability(), store).acquire(_version(), _false)

    fallback = _FakeStockFallback(_Store())
    stock_result = await fallback.acquire(_version(), _false)

    generative_fields = set(generative_result[0].model_dump().keys())
    stock_fields = set(stock_result[0].model_dump().keys())
    assert generative_fields == stock_fields
    assert type(generative_result[0]) is type(stock_result[0])


@pytest.mark.asyncio
async def test_partial_generative_persistence_never_produces_a_mixed_final_artifact_set():
    version = _version()
    store = _Store()

    # 1. Seed scene 1 with a valid, reconcilable Stability artifact, as if a
    # prior acquire() call had already generated it successfully.
    scene_one = version.storyboard.scenes[0]
    prompt = build_generative_prompt(version, scene_one)
    fingerprint = generative_scene_fingerprint(
        version, scene_one, prompt, model="sd3.5-large", aspect_ratio="9:16", output_format="jpeg"
    )
    image = ImageProcessor().normalize(_jpeg())
    store.put(
        version,
        1,
        image,
        {
            "campaign_id": str(version.campaign_id),
            "campaign_version": version.campaign_version,
            "scene_number": 1,
            "storyboard_fingerprint": fingerprint,
            "normalized_checksum": image.checksum_sha256,
            "provider": "stability-ai",
            "model": "sd3.5-large",
        },
    )

    # 2 & 3. Scene 2 fails with a fallback-approved error; whole-version
    # fallback must return all 3 Pexels artifacts, discarding scene 1's
    # pre-existing, still-valid Stability artifact.
    stability = _Stability(fail_scenes={2})
    fallback = _FakeStockFallback(store)
    result = await _pipeline(stability, store, fallback).acquire(version, _false)

    assert {artifact.provider for artifact in result} == {"pexels"}
    assert len(result) == 3
    assert 1 not in stability.scene_calls  # scene 1's cached Stability artifact was never touched by Stability here

    # 4. The store now holds Pexels-shaped metadata for scene 1 too --
    # proof the fallback pipeline's put() overwrote the shared per-scene key.
    assert store.metadata[1]["pexels_photo_id"] == 101
    assert "model" not in store.metadata[1]

    # 5. Retry: Stability now succeeds for all 3 scenes. Scene 1 must NOT be
    # served from its old, now-overwritten cache -- a fresh generate() call
    # is required for it too, and the final result must be all-Stability.
    stability_retry = _Stability()
    retry_result = await _pipeline(stability_retry, store, fallback).acquire(version, _false)
    assert stability_retry.scene_calls == [1, 2, 3]
    assert {artifact.provider for artifact in retry_result} == {"stability-ai"}

    # 6. Simulate scene 2's cache being invalidated again (e.g. a later
    # revision touching only that scene) and Stability failing on the
    # retry -- even though scenes 1 and 3 are still validly Stability-cached
    # at this point, the result must once more be all-Pexels, never a mix
    # of cached-Stability + fresh-Pexels artifacts.
    del store.saved[2]
    del store.metadata[2]
    stability_retry_fail = _Stability(fail_scenes={2})
    second_fallback_result = await _pipeline(stability_retry_fail, store, fallback).acquire(version, _false)
    assert {artifact.provider for artifact in second_fallback_result} == {"pexels"}

    # 7. No assertion above ever observed a mixed set -- every result was a
    # single-provider set of exactly 3 artifacts.
