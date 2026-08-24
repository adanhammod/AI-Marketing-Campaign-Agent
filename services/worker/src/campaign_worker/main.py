import asyncio
import signal
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
import httpx
import uvicorn
from botocore.config import Config  # type: ignore[import-untyped]

from .audio.normalizer import AudioNormalizer
from .audio.pipeline import PollyVoicePipeline
from .audio.processor import AudioProcessor
from .config import Settings
from .consumer.sqs_consumer import SQSConsumer
from .errors import ConfigurationError
from .graph.creative_plan_provider import (
    BedrockCreativePlanProvider,
    DeterministicCreativePlanProvider,
    FallbackCreativePlanProvider,
)
from .health import build_health_app
from .images.generative_pipeline import GenerativeImagePipeline
from .images.pipeline import ImageAssetPipeline, StockImagePipeline
from .images.processor import ImageProcessor
from .images.query_generator import BedrockQueryGenerator
from .logging import configure_logging
from .package.pipeline import PackageAssetPipeline, S3PackagePipeline
from .providers.base import CreativePlanProvider, VideoProvider
from .providers.mock_image_provider import MockImageProvider
from .providers.mock_package_pipeline import MockPackagePipeline
from .providers.mock_video_provider import MockVideoProvider
from .providers.mock_voice_provider import MockVoiceProvider
from .providers.cloudflare_flux_client import CloudflareFluxClient
from .providers.pexels_client import PexelsPhotoClient
from .repositories.dynamodb_workflow_repository import DynamoDBWorkflowRepository
from .services.job_processor import GraphJobProcessor
from .storage.s3_artifact_store import S3ArtifactStore
from .video.ffmpeg_renderer import FfmpegVideoRenderer
from .video.hyperframes_renderer import HyperFramesVideoRenderer
from .video.models import VideoRenderer
from .video.pipeline import FfmpegVideoPipeline, VideoAssetPipeline


def build_consumer(
    settings: Settings,
    sqs_client: Any | None = None,
    dynamodb_client: Any | None = None,
    bedrock_client: Any | None = None,
    s3_client: Any | None = None,
    polly_client: Any | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> SQSConsumer:
    settings.validate()
    config = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 0})
    # SQS long-polls for up to settings.wait_time_seconds (20s max); it gets its own Config
    # with a read_timeout that has real headroom above that, isolated from the shared
    # config above so DynamoDB/Bedrock/S3/Polly timeout behavior is unaffected.
    sqs_config = Config(
        connect_timeout=settings.sqs_connect_timeout_seconds,
        read_timeout=settings.sqs_read_timeout_seconds,
        retries={"max_attempts": 0},
    )
    # Polly synthesis is a single call with no inherent expected-wait like SQS
    # long polling, but real production timeouts have been observed even on
    # short (~30 word) narrations -- it gets its own Config with more read
    # headroom and a small bounded retry budget, isolated from the shared
    # config so DynamoDB/Bedrock/S3 timeout behavior is unaffected. The
    # synthesize_speech()/AudioStream call runs via asyncio.to_thread (see
    # audio/pipeline.py), so a worst-case retry-exhausted duration here doesn't
    # block the SQS consumer's heartbeat loop from extending the visibility
    # timeout/lease in the meantime.
    polly_config = Config(
        connect_timeout=settings.polly_connect_timeout_seconds,
        read_timeout=settings.polly_read_timeout_seconds,
        retries={"mode": settings.polly_retry_mode, "max_attempts": settings.polly_max_attempts},
    )
    sqs = sqs_client or boto3.client(
        "sqs", region_name=settings.aws_region, endpoint_url=settings.endpoint_url, config=sqs_config
    )
    dynamodb = dynamodb_client or boto3.client(
        "dynamodb", region_name=settings.aws_region, endpoint_url=settings.endpoint_url, config=config
    )
    repository = DynamoDBWorkflowRepository(dynamodb, settings.table_name or "")
    # Independent of the image/voice/video real-vs-mock branch below: creative-plan
    # generation runs regardless of which image provider ends up being used, and
    # BedrockCreativePlanProvider failures already fall back to the deterministic
    # generator on their own (see FallbackCreativePlanProvider), so there is no
    # ConfigurationError path to catch here.
    creative_plan_provider: CreativePlanProvider = DeterministicCreativePlanProvider()
    if settings.bedrock_creative_plan_model_id:
        creative_plan_bedrock = bedrock_client or boto3.client(
            "bedrock-runtime", region_name=settings.aws_region, config=config
        )
        creative_plan_provider = FallbackCreativePlanProvider(
            primary=BedrockCreativePlanProvider(creative_plan_bedrock, settings.bedrock_creative_plan_model_id),
            fallback=DeterministicCreativePlanProvider(),
        )
    if (
        settings.artifact_bucket
        and settings.bedrock_image_query_model_id
        and (settings.pexels_api_key or (settings.cloudflare_account_id and settings.cloudflare_api_token))
    ):
        settings.validate_image_pipeline()
        settings.validate_voice_pipeline()
        bedrock = bedrock_client or boto3.client("bedrock-runtime", region_name=settings.aws_region, config=config)
        s3 = s3_client or boto3.client(
            "s3", region_name=settings.aws_region, endpoint_url=settings.endpoint_url, config=config
        )
        polly = polly_client or boto3.client("polly", region_name=settings.aws_region, config=polly_config)
        artifact_store = S3ArtifactStore(s3, settings.artifact_bucket)

        stock_pipeline: StockImagePipeline | None = None
        if settings.pexels_api_key:
            pexels_http = http_client or httpx.AsyncClient(
                timeout=httpx.Timeout(settings.image_http_timeout_seconds), follow_redirects=True
            )
            stock_pipeline = StockImagePipeline(
                BedrockQueryGenerator(bedrock, settings.bedrock_image_query_model_id),
                PexelsPhotoClient(settings.pexels_api_key, pexels_http, per_page=settings.pexels_candidate_count),
                ImageProcessor(settings.image_max_download_bytes),
                artifact_store,
            )

        image_pipeline: ImageAssetPipeline
        if settings.image_provider_mode == "stock":
            assert stock_pipeline is not None  # guaranteed by validate_image_pipeline()
            image_pipeline = stock_pipeline
        else:
            # FLUX-schnell generation gets its own client rather than sharing
            # the short-timeout Pexels client, matching this module's
            # per-provider-client convention (previously used for Stability).
            cloudflare_http = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.cloudflare_http_timeout_seconds), follow_redirects=True
            )
            assert settings.cloudflare_account_id is not None  # guaranteed by validate_image_pipeline()
            assert settings.cloudflare_api_token is not None  # guaranteed by validate_image_pipeline()
            image_pipeline = GenerativeImagePipeline(
                CloudflareFluxClient(
                    settings.cloudflare_account_id,
                    settings.cloudflare_api_token,
                    cloudflare_http,
                    model=settings.cloudflare_flux_model,
                    steps=settings.cloudflare_flux_steps,
                    max_download_bytes=settings.image_max_download_bytes,
                ),
                stock_pipeline if settings.image_pexels_fallback_enabled else None,
                ImageProcessor(settings.image_max_download_bytes),
                artifact_store,
                model=settings.cloudflare_flux_model,
                aspect_ratio=settings.cloudflare_flux_aspect_ratio,
                output_format="jpeg",
            )

        voice_pipeline = PollyVoicePipeline(
            polly,
            artifact_store,
            AudioProcessor(),
            AudioNormalizer(
                ffmpeg_path=settings.ffmpeg_path,
                timeout_seconds=settings.audio_normalize_timeout_seconds,
            ),
            voice_id=settings.polly_voice_id,
            engine=settings.polly_engine,
            ffmpeg_path=settings.ffmpeg_path,
            ffprobe_path=settings.ffprobe_path,
            duration_check_timeout_seconds=settings.audio_normalize_timeout_seconds,
        )
        video_provider: VideoProvider | VideoAssetPipeline
        try:
            settings.validate_video_pipeline()
            renderer: VideoRenderer
            if settings.video_renderer_mode == "hyperframes":
                renderer = HyperFramesVideoRenderer(
                    npx_path=settings.npx_path,
                    render_timeout_seconds=settings.video_render_timeout_seconds,
                )
            else:
                renderer = FfmpegVideoRenderer(
                    ffmpeg_path=settings.ffmpeg_path,
                    render_timeout_seconds=settings.video_render_timeout_seconds,
                )
            video_provider = FfmpegVideoPipeline(
                s3,
                artifact_store,
                settings.artifact_bucket,
                ffmpeg_path=settings.ffmpeg_path,
                ffprobe_path=settings.ffprobe_path,
                render_timeout_seconds=settings.video_render_timeout_seconds,
                max_download_bytes=settings.video_max_download_bytes,
                renderer=renderer,
                music_path=Path(settings.cinematic_music_path) if settings.cinematic_music_path else None,
                sfx_library_root=Path(settings.sfx_library_path) if settings.sfx_library_path else None,
            )
        except ConfigurationError:
            video_provider = MockVideoProvider()
        package_pipeline: PackageAssetPipeline = S3PackagePipeline(
            s3,
            artifact_store,
            settings.artifact_bucket,
            max_download_bytes=settings.video_max_download_bytes,
            max_package_bytes=settings.package_max_bytes,
        )
        processor = GraphJobProcessor(
            repository,
            image_pipeline,
            voice_pipeline,
            video_provider,
            package_pipeline=package_pipeline,
            creative_plan_provider=creative_plan_provider,
        )
    else:
        # Outside local/test, silently falling back to mock providers would hide a
        # completely unconfigured deployment (e.g. a forgotten campaign-secrets Secret)
        # behind campaigns that "succeed" without ever calling a real provider -- fail
        # fast instead, the same way validate_image_pipeline()/validate_voice_pipeline()
        # above fail fast on a *partially* configured real pipeline.
        if settings.environment not in {"local", "test"}:
            raise ConfigurationError(
                "no image/voice pipeline is configured: CAMPAIGN_ARTIFACT_BUCKET, "
                "BEDROCK_IMAGE_QUERY_MODEL_ID, and either PEXELS_API_KEY or Cloudflare "
                "credentials are all required outside local/test environments -- refusing "
                "to silently run with mock providers in a deployed environment"
            )
        processor = GraphJobProcessor(
            repository,
            MockImageProvider(),
            MockVoiceProvider(),
            MockVideoProvider(),
            package_pipeline=MockPackagePipeline(),
            creative_plan_provider=creative_plan_provider,
        )
    return SQSConsumer(sqs, repository, processor, settings)


async def serve() -> None:
    settings = Settings.from_env()
    configure_logging()
    consumer = build_consumer(settings)
    health_app = build_health_app(consumer, settings)
    server = uvicorn.Server(uvicorn.Config(health_app, host="0.0.0.0", port=settings.health_port, log_level="warning"))

    async def shutdown() -> None:
        server.should_exit = True
        await consumer.shutdown()

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, lambda: asyncio.create_task(shutdown()))
    await asyncio.gather(consumer.run(), server.serve())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
