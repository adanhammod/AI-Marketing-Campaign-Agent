import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from campaign_contracts.artifacts import ImageArtifactReference
from campaign_contracts.campaign import CampaignVersion, StoryboardScene
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.providers.stability_client import GeneratedImage
from campaign_worker.storage.artifact_store import ArtifactStore, StoredImage

from .generative_prompt import GenerativePrompt, build_generative_prompt
from .pipeline import ImageAssetPipeline
from .processor import ImageProcessor

_GENERATIVE_PIPELINE_VERSION = 1

# Errors approved for automatic fallback to the stock (Pexels) pipeline.
# STORAGE_UNAVAILABLE and PROVIDER_POLICY_REJECTION are deliberately excluded:
# an S3 outage should fail the job rather than silently degrade, and a
# content-policy rejection is a signal worth surfacing, not masking with a
# stock photo.
_FALLBACK_APPROVED_CODES = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "IMAGE_PROVIDER_UNAVAILABLE",
        "PROVIDER_THROTTLED",
        "INVALID_PROVIDER_OUTPUT",
        "ARTIFACT_VALIDATION_FAILED",
    }
)


def _is_approved_for_fallback(error: WorkflowOperationError) -> bool:
    return error.code in _FALLBACK_APPROVED_CODES


class GenerativeImageClient(Protocol):
    async def generate(self, positive_prompt: str, negative_prompt: str) -> GeneratedImage: ...


def generative_scene_fingerprint(
    version: CampaignVersion,
    scene: StoryboardScene,
    prompt: GenerativePrompt,
    *,
    model: str,
    aspect_ratio: str,
    output_format: str,
) -> str:
    # Per-scene (not whole-version like the stock/video fingerprints) so
    # editing one scene's visual_prompt only re-spends generation cost on
    # that scene. The payload shape (model/aspect_ratio/generation_version
    # keys) never overlaps with the stock pipeline's {brief, storyboard}
    # payload, so a Pexels artifact can never satisfy this reconcile check
    # or vice versa.
    payload = {
        "pipeline": "generative-image",
        "model": model,
        "aspect_ratio": aspect_ratio,
        "output_format": output_format,
        "generation_version": _GENERATIVE_PIPELINE_VERSION,
        "positive_prompt": prompt.positive,
        "negative_prompt": prompt.negative,
        "business_name": version.brief.business_name,
        "scene_number": scene.scene_number,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class GenerativeImagePipeline:
    def __init__(
        self,
        stability: GenerativeImageClient,
        fallback: ImageAssetPipeline | None,
        processor: ImageProcessor,
        store: ArtifactStore,
        *,
        model: str,
        aspect_ratio: str,
        output_format: str,
    ) -> None:
        self._stability = stability
        self._fallback = fallback
        self._processor = processor
        self._store = store
        self._model = model
        self._aspect_ratio = aspect_ratio
        self._output_format = output_format

    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> list[ImageArtifactReference]:
        if version.storyboard is None:
            raise ValueError("image acquisition requires a storyboard")

        prompts: dict[int, GenerativePrompt] = {}
        fingerprints: dict[int, str] = {}
        stored: dict[int, StoredImage | None] = {}
        for scene in version.storyboard.scenes:
            prompt = build_generative_prompt(version, scene)
            fingerprint = generative_scene_fingerprint(
                version,
                scene,
                prompt,
                model=self._model,
                aspect_ratio=self._aspect_ratio,
                output_format=self._output_format,
            )
            prompts[scene.scene_number] = prompt
            fingerprints[scene.scene_number] = fingerprint
            stored[scene.scene_number] = self._store.reconcile(version, scene.scene_number, fingerprint)

        if all(stored.values()):
            return [self._reference(version, n, stored[n]) for n in (1, 2, 3) if stored[n] is not None]

        try:
            for scene in version.storyboard.scenes:
                if stored[scene.scene_number] is not None:
                    continue
                await self._checkpoint(is_cancelled, "between_scenes")
                await self._checkpoint(is_cancelled, "before_generate")
                prompt = prompts[scene.scene_number]
                generated = await self._stability.generate(prompt.positive, prompt.negative)
                await self._checkpoint(is_cancelled, "before_image_processing")
                image = self._processor.normalize(generated.data)
                await self._checkpoint(is_cancelled, "before_s3_upload")
                metadata: dict[str, object] = {
                    "campaign_id": str(version.campaign_id),
                    "campaign_version": version.campaign_version,
                    "scene_number": scene.scene_number,
                    "storyboard_fingerprint": fingerprints[scene.scene_number],
                    "normalized_checksum": image.checksum_sha256,
                    "source_checksum": image.source_checksum_sha256,
                    "provider": "cloudflare-workers-ai",
                    "model": self._model,
                    "aspect_ratio": self._aspect_ratio,
                    "output_format": self._output_format,
                    "generation_version": _GENERATIVE_PIPELINE_VERSION,
                    "prompt_fingerprint": prompt.fingerprint,
                    "seed": generated.seed,
                }
                stored[scene.scene_number] = self._store.put(version, scene.scene_number, image, metadata)
        except WorkflowOperationError as exc:
            if _is_approved_for_fallback(exc) and self._fallback is not None:
                # Whole-version fallback: discard any scenes already
                # generated or served from cache in this call and return
                # exactly the stock pipeline's result, so the returned list
                # can never mix providers. StockImagePipeline overwrites
                # each scene's shared S3 key with a fresh Pexels artifact,
                # including scenes this attempt already completed.
                return await self._fallback.acquire(version, is_cancelled)
            raise

        if any(stored[n] is None for n in (1, 2, 3)):
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "image acquisition did not produce three images", retryable=True
            )
        return [self._reference(version, n, stored[n]) for n in (1, 2, 3) if stored[n] is not None]

    @staticmethod
    async def _checkpoint(is_cancelled: Callable[[], Awaitable[bool]], phase: str) -> None:
        if await is_cancelled():
            raise NodeCancelled(f"generate_images:{phase}")

    @staticmethod
    def _reference(version: CampaignVersion, scene_number: int, stored: StoredImage | None) -> ImageArtifactReference:
        assert stored is not None
        return ImageArtifactReference(
            artifact_id=stored.artifact_id,
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/jpeg",
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.checksum_sha256,
            scene_number=scene_number,
            attribution=None,
            created_at=stored.created_at,
            provider="cloudflare-workers-ai",
        )
