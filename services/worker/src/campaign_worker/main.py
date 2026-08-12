import asyncio
import signal
from typing import Any

import boto3  # type: ignore[import-untyped]
import httpx
import uvicorn
from botocore.config import Config  # type: ignore[import-untyped]

from .audio.pipeline import PollyVoicePipeline
from .audio.processor import AudioProcessor
from .config import Settings
from .consumer.sqs_consumer import SQSConsumer
from .errors import ConfigurationError
from .health import build_health_app
from .images.pipeline import StockImagePipeline
from .images.processor import ImageProcessor
from .images.query_generator import BedrockQueryGenerator
from .logging import configure_logging
from .providers.base import VideoProvider
from .providers.mock_image_provider import MockImageProvider
from .providers.mock_video_provider import MockVideoProvider
from .providers.mock_voice_provider import MockVoiceProvider
from .providers.pexels_client import PexelsPhotoClient
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
    if settings.artifact_bucket and settings.pexels_api_key and settings.bedrock_image_query_model_id:
        settings.validate_image_pipeline()
        settings.validate_voice_pipeline()
        bedrock = bedrock_client or boto3.client("bedrock-runtime", region_name=settings.aws_region, config=config)
        s3 = s3_client or boto3.client(
            "s3", region_name=settings.aws_region, endpoint_url=settings.endpoint_url, config=config
        )
        polly = polly_client or boto3.client("polly", region_name=settings.aws_region, config=config)
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.image_http_timeout_seconds), follow_redirects=True
        )
        artifact_store = S3ArtifactStore(s3, settings.artifact_bucket)
        image_pipeline = StockImagePipeline(
            BedrockQueryGenerator(bedrock, settings.bedrock_image_query_model_id),
            PexelsPhotoClient(settings.pexels_api_key, client, per_page=settings.pexels_candidate_count),
            ImageProcessor(settings.image_max_download_bytes),
            artifact_store,
        )
        voice_pipeline = PollyVoicePipeline(
            polly,
            artifact_store,
            AudioProcessor(),
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
        processor = GraphJobProcessor(repository, image_pipeline, voice_pipeline, video_provider)
    else:
        processor = GraphJobProcessor(repository, MockImageProvider(), MockVoiceProvider(), MockVideoProvider())
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
