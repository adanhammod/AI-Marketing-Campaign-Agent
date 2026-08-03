import asyncio
import signal
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from .config import Settings
from .consumer.sqs_consumer import SQSConsumer
from .logging import configure_logging
from .repositories.dynamodb_workflow_repository import DynamoDBWorkflowRepository
from .services.job_processor import NoOpJobProcessor


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
    return SQSConsumer(sqs, repository, NoOpJobProcessor(), settings)


async def serve() -> None:
    settings = Settings.from_env()
    configure_logging()
    consumer = build_consumer(settings)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, lambda: asyncio.create_task(consumer.shutdown()))
    await consumer.run()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
