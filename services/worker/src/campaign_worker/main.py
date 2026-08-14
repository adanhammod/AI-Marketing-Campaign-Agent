import asyncio
import signal
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
from .health import build_health_app
from .images.generative_pipeline import GenerativeImagePipeline
from .images.pipeline import ImageAssetPipeline, StockImagePipeline
from .images.processor import ImageProcessor
from .images.query_generator import BedrockQueryGenerator
from .logging import configure_logging
from .package.pipeline import PackageAssetPipeline, S3PackagePipeline
from .providers.base import VideoProvider
from .providers.mock_image_provider import MockImageProvider
from .providers.mock_package_pipeline import MockPackagePipeline
from .providers.mock_video_provider import MockVideoProvider
from .providers.mock_voice_provider import MockVoiceProvider
from .providers.pexels_client import PexelsPhotoClient
from .providers.stability_client import StabilityImageClient
from .repositories.dynamodb_workflow_repository import DynamoDBWorkflowRepository
from .services.job_processor import GraphJobProcessor
from .storage.s3_artifact_store import S3ArtifactStore
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
    sqs = sqs_client or boto3.client(
        "sqs", region_name=settings.aws_region, endpoint_url=settings.endpoint_url, config=config
    )
    dynamodb = dynamodb_client or boto3.client(
        "dynamodb", region_name=settings.aws_region, endpoint_url=settings.endpoint_url, config=config
    )
    repository = DynamoDBWorkflowRepository(dynamodb, settings.table_name or "")
    if (
        settings.artifact_bucket
        and settings.bedrock_image_query_model_id
        and (settings.pexels_api_key or settings.stability_api_key)
    ):
        settings.validate_image_pipeline()
        settings.validate_voice_pipeline()
        bedrock = bedrock_client or boto3.client("bedrock-runtime", region_name=settings.aws_region, config=config)
        s3 = s3_client or boto3.client(
            "s3", region_name=settings.aws_region, endpoint_url=settings.endpoint_url, config=config
        )
        polly = polly_client or boto3.client("polly", region_name=settings.aws_region, config=config)
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
            # Stability generation takes materially longer than a Pexels
            # search/download, so it gets its own client with a longer
            # timeout rather than sharing the short-timeout Pexels client.
            stability_http = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.stability_http_timeout_seconds), follow_redirects=True
            )
            assert settings.stability_api_key is not None  # guaranteed by validate_image_pipeline()
            image_pipeline = GenerativeImagePipeline(
                StabilityImageClient(
                    settings.stability_api_key,
                    stability_http,
                    model=settings.stability_image_model,
                    aspect_ratio=settings.stability_image_aspect_ratio,
                    output_format=settings.stability_image_output_format,
                    max_download_bytes=settings.image_max_download_bytes,
                ),
                stock_pipeline if settings.image_pexels_fallback_enabled else None,
                ImageProcessor(settings.image_max_download_bytes),
                artifact_store,
                model=settings.stability_image_model,
                aspect_ratio=settings.stability_image_aspect_ratio,
                output_format=settings.stability_image_output_format,
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
        )
        video_provider: VideoProvider | VideoAssetPipeline
        try:
            settings.validate_video_pipeline()
            video_provider = FfmpegVideoPipeline(
                s3,
                artifact_store,
                settings.artifact_bucket,
                ffmpeg_path=settings.ffmpeg_path,
                ffprobe_path=settings.ffprobe_path,
                render_timeout_seconds=settings.video_render_timeout_seconds,
                max_download_bytes=settings.video_max_download_bytes,
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
            repository, image_pipeline, voice_pipeline, video_provider, package_pipeline=package_pipeline
        )
    else:
        processor = GraphJobProcessor(
            repository,
            MockImageProvider(),
            MockVoiceProvider(),
            MockVideoProvider(),
            package_pipeline=MockPackagePipeline(),
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
