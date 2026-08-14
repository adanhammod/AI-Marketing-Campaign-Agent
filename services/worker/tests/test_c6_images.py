import hashlib
import io
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from campaign_contracts.api import CampaignCreationRequest
from campaign_contracts.campaign import CampaignConstraints, CampaignVersion, RetryMetadata, Storyboard, StoryboardScene
from campaign_contracts.enums import CampaignStatus, WorkflowStep
from PIL import Image

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph import nodes
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.images.models import PhotoCandidate, SceneSearchQuery
from campaign_worker.images.pipeline import (
    StockImagePipeline,
    campaign_storyboard_fingerprint,
    deterministic_artifact_id,
)
from campaign_worker.images.processor import ImageProcessor
from campaign_worker.images.query_generator import BedrockQueryGenerator, deterministic_queries
from campaign_worker.images.selector import rank_candidates
from campaign_worker.providers.pexels_client import PexelsPhotoClient
from campaign_worker.storage.artifact_store import StoredImage
from campaign_worker.storage.s3_artifact_store import S3ArtifactStore


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


def _jpeg(width: int = 1200, height: int = 2000, color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="JPEG")
    return output.getvalue()


class _Bedrock:
    def __init__(self, payload: object = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    def invoke_model(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        body = json.dumps({"output": {"message": {"content": [{"text": json.dumps(self.payload)}]}}})
        return {"body": io.BytesIO(body.encode())}


@pytest.mark.asyncio
async def test_bedrock_generates_all_three_queries_in_one_call_and_falls_back_on_invalid_output():
    version = _version()
    valid = _Bedrock([{"scene_number": n, "query": f"coffee lifestyle {n}"} for n in (1, 2, 3)])
    generated = await BedrockQueryGenerator(valid, "model-id").generate(version, lambda: _false())
    assert [query.query for query in generated.queries] == [f"coffee lifestyle {n}" for n in (1, 2, 3)]
    assert valid.calls == 1
    invalid = await BedrockQueryGenerator(_Bedrock({"bad": "shape"}), "model-id").generate(version, lambda: _false())
    assert invalid.fallback_used is True
    assert invalid.queries == deterministic_queries(version)


async def _false() -> bool:
    return False


def test_deterministic_query_fallback_uses_storyboard_and_campaign_context():
    queries = deterministic_queries(_version())
    assert [q.scene_number for q in queries] == [1, 2, 3]
    assert all("cold brew" in q.query.lower() for q in queries)


@pytest.mark.asyncio
async def test_pexels_auth_request_parsing_zero_results_and_sanitized_errors():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["query"] == "empty":
            return httpx.Response(200, json={"photos": []})
        return httpx.Response(
            200,
            json={
                "photos": [
                    {
                        "id": 7,
                        "width": 1200,
                        "height": 2000,
                        "url": "https://www.pexels.com/photo/7/",
                        "photographer": "A",
                        "photographer_id": 9,
                        "photographer_url": "https://www.pexels.com/a/",
                        "alt": "coffee",
                        "src": {"portrait": "https://images.pexels.com/7.jpg"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pexels = PexelsPhotoClient("secret", client, per_page=5)
        photos = await pexels.search("coffee")
        assert photos[0].photo_id == 7
        assert await pexels.search("empty") == []
    assert seen[0].headers["Authorization"] == "secret"
    assert seen[0].url.params["orientation"] == "portrait"
    assert seen[0].url.params["per_page"] == "5"

    for status, code in [
        (401, "IMAGE_PROVIDER_UNAVAILABLE"),
        (403, "IMAGE_PROVIDER_UNAVAILABLE"),
        (429, "PROVIDER_THROTTLED"),
        (500, "IMAGE_PROVIDER_UNAVAILABLE"),
    ]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, response_status=status: httpx.Response(response_status))
        ) as client:
            with pytest.raises(WorkflowOperationError) as error:
                await PexelsPhotoClient("secret", client).search("coffee")
            assert error.value.code == code
            assert "secret" not in str(error.value)


def test_deterministic_selection_prefers_portrait_ratio_area_and_avoids_duplicates():
    photos = [
        PhotoCandidate(photo_id=2, width=1080, height=1920, source_url="https://x/2"),
        PhotoCandidate(photo_id=1, width=2160, height=3840, source_url="https://x/1"),
        PhotoCandidate(photo_id=3, width=1920, height=1080, source_url="https://x/3"),
    ]
    assert [photo.photo_id for photo in rank_candidates(photos, set())] == [1, 2, 3]
    assert [photo.photo_id for photo in rank_candidates(photos, {1})] == [2, 3]


def test_processor_rejects_corrupt_and_oversized_content_and_normalizes_deterministically():
    processor = ImageProcessor(max_download_bytes=10_000_000)
    first = processor.normalize(_jpeg())
    second = processor.normalize(_jpeg())
    assert first.width == 1080 and first.height == 1920
    assert first.data == second.data
    assert first.checksum_sha256 == hashlib.sha256(first.data).hexdigest()
    with pytest.raises(WorkflowOperationError, match="valid image"):
        processor.normalize(b"not-image")
    with pytest.raises(WorkflowOperationError, match="size limit"):
        ImageProcessor(max_download_bytes=3).normalize(b"four")


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("missing")
        return {"Body": io.BytesIO(self.objects[Key])}


def test_s3_keys_metadata_artifact_ids_and_retry_reconciliation():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    image = ImageProcessor().normalize(_jpeg())
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": 2,
        "scene_number": 1,
        "storyboard_fingerprint": campaign_storyboard_fingerprint(version),
        "normalized_checksum": image.checksum_sha256,
        "pexels_photo_id": 7,
        "photographer_name": "Creator",
        "photographer_profile_url": "https://www.pexels.com/@creator",
        "pexels_photo_page_url": "https://www.pexels.com/photo/example-7/",
    }
    stored = store.put(version, 1, image, metadata)
    assert stored.artifact_id == deterministic_artifact_id(version.campaign_id, 2, 1)
    assert f"campaigns/{version.campaign_id}/versions/2/images/scene-1.jpg" in s3.objects
    assert store.reconcile(version, 1, campaign_storyboard_fingerprint(version)) == stored


def test_s3_store_reconciles_generative_metadata_without_pexels_specific_keys():
    version = _version()
    s3 = _S3()
    store = S3ArtifactStore(s3, "private-bucket")
    image = ImageProcessor().normalize(_jpeg())
    fingerprint = "generative-fingerprint-abc123"
    metadata = {
        "campaign_id": str(version.campaign_id),
        "campaign_version": 2,
        "scene_number": 1,
        "storyboard_fingerprint": fingerprint,
        "normalized_checksum": image.checksum_sha256,
        "provider": "stability-ai",
        "model": "sd3.5-large",
        "aspect_ratio": "9:16",
        "output_format": "jpeg",
        "generation_version": 1,
        "prompt_fingerprint": "abc123",
        "seed": 42,
    }
    stored = store.put(version, 1, image, metadata)
    assert stored.pexels_photo_id is None

    # Before the fix, this KeyError'd on metadata["pexels_photo_id"] and was
    # silently swallowed as a cache miss, defeating caching entirely for
    # generative images.
    reconciled = store.reconcile(version, 1, fingerprint)
    assert reconciled is not None
    assert reconciled == stored


def test_s3_store_attribution_returns_none_for_stability_metadata():
    metadata = {
        "provider": "stability-ai",
        "model": "sd3.5-large",
    }
    assert S3ArtifactStore._attribution(metadata) is None


class _QueryGenerator:
    async def generate(self, version, is_cancelled):
        from campaign_worker.images.models import QueryGenerationResult

        return QueryGenerationResult(
            queries=[SceneSearchQuery(scene_number=n, query=f"q{n}") for n in (1, 2, 3)],
            provider="fallback",
            model=None,
            fallback_used=True,
        )


class _Photos:
    def __init__(self) -> None:
        self.search_calls = 0
        self.downloaded: list[int] = []

    async def search(self, query):
        self.search_calls += 1
        return [
            PhotoCandidate(
                photo_id=int(query[-1]) * 10 + suffix,
                width=1200,
                height=2000,
                source_url=f"https://x/{query[-1]}{suffix}",
                photo_page_url=f"https://www.pexels.com/photo/example-{query[-1]}{suffix}/",
                photographer="Creator",
                photographer_url="https://www.pexels.com/@creator",
                photographer_id=9,
            )
            for suffix in (1, 2)
        ]

    async def download(self, photo, max_bytes):
        self.downloaded.append(photo.photo_id)
        if photo.photo_id % 10 == 1:
            return b"bad"
        return _jpeg()


class _Store:
    def __init__(self) -> None:
        self.saved: dict[int, StoredImage] = {}

    def reconcile(self, version, scene_number, fingerprint):
        return self.saved.get(scene_number)

    def put(self, version, scene_number, image, metadata):
        stored = StoredImage(
            deterministic_artifact_id(version.campaign_id, version.campaign_version, scene_number),
            image.checksum_sha256,
            len(image.data),
            datetime.now(UTC),
            int(metadata["pexels_photo_id"]),
            S3ArtifactStore._attribution(metadata),
        )
        self.saved[scene_number] = stored
        return stored


@pytest.mark.asyncio
async def test_pipeline_candidate_fallback_partial_visibility_exactly_three_and_retry_reconciliation():
    version = _version()
    photos, store = _Photos(), _Store()
    pipeline = StockImagePipeline(_QueryGenerator(), photos, ImageProcessor(), store)
    result = await pipeline.acquire(version, _false)
    assert len(result) == 3
    assert len({artifact.artifact_id for artifact in result}) == 3
    assert photos.search_calls == 3
    assert len(store.saved) == 3
    second = await pipeline.acquire(version, _false)
    assert second == result

    assert [artifact.scene_number for artifact in result] == [1, 2, 3]
    assert all(artifact.provider == "pexels" for artifact in result)
    assert [artifact.attribution.provider_asset_id for artifact in result if artifact.attribution] == [
        "12",
        "22",
        "32",
    ]
    assert all(
        artifact.attribution.attribution_text == "Photo by Creator on Pexels"
        for artifact in result
        if artifact.attribution
    )

    assert photos.search_calls == 3


@pytest.mark.asyncio
async def test_bedrock_and_pipeline_cancellation_checkpoints_stop_external_work():
    bedrock = _Bedrock([{"scene_number": n, "query": f"q{n}"} for n in (1, 2, 3)])

    async def cancelled() -> bool:
        return True

    with pytest.raises(NodeCancelled, match="before_bedrock"):
        await BedrockQueryGenerator(bedrock, "model").generate(_version(), cancelled)
    assert bedrock.calls == 0

    photos = _Photos()
    with pytest.raises(NodeCancelled, match="between_scenes"):
        await StockImagePipeline(_QueryGenerator(), photos, ImageProcessor(), _Store()).acquire(_version(), cancelled)
    assert photos.search_calls == 0


@pytest.mark.asyncio
async def test_pexels_timeout_malformed_response_and_bounded_stream_are_typed():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(WorkflowOperationError) as error:
            await PexelsPhotoClient("secret", client).search("coffee")
        assert error.value.code == "PROVIDER_TIMEOUT"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"wrong": []}))
    ) as client:
        with pytest.raises(WorkflowOperationError) as error:
            await PexelsPhotoClient("secret", client).search("coffee")
        assert error.value.code == "INVALID_PROVIDER_OUTPUT"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"too-large"))
    ) as client:
        photo = PhotoCandidate(photo_id=1, width=10, height=20, source_url="https://images.pexels.com/1")
        with pytest.raises(WorkflowOperationError) as error:
            await PexelsPhotoClient("secret", client).download(photo, max_bytes=3)
        assert error.value.code == "ARTIFACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_provider_and_storage_error_codes_survive_graph_failure_boundary():
    for code in ("IMAGE_PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT", "STORAGE_UNAVAILABLE"):
        result = await nodes.handle_failure(
            {"version": _version()},
            WorkflowOperationError(code, "safe failure", retryable=True),
            step=WorkflowStep.IMAGES,
        )
        assert result["version"].error.code == code


def test_s3_reconciliation_outage_is_not_treated_as_a_cache_miss():
    version = _version()
    store = S3ArtifactStore(_S3(), "private-bucket")
    with pytest.raises(WorkflowOperationError) as error:
        store.reconcile(version, 1, campaign_storyboard_fingerprint(version))
    assert error.value.code == "STORAGE_UNAVAILABLE"
