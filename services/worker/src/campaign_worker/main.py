import asyncio
import signal
from typing import Any

import boto3  # type: ignore[import-untyped]
import uvicorn
from botocore.config import Config  # type: ignore[import-untyped]

from .config import Settings
from .consumer.sqs_consumer import SQSConsumer
from .health import build_health_app
from .logging import configure_logging
from .providers.mock_image_provider import MockImageProvider
from .providers.mock_video_provider import MockVideoProvider
from .providers.mock_voice_provider import MockVoiceProvider
from .repositories.dynamodb_workflow_repository import DynamoDBWorkflowRepository
from .services.job_processor import GraphJobProcessor


def build_consumer(
    settings: Settings, sqs_client: Any | None = None, dynamodb_client: Any | None = None
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
