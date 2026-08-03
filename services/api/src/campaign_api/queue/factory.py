from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from campaign_api.config import Settings

from .in_memory_job_queue import InMemoryJobQueue
from .job_queue import JobQueue
from .sqs_job_queue import SQSJobQueue


def create_job_queue(settings: Settings, client: Any | None = None) -> JobQueue:
    settings.validate()
    if settings.queue_backend == "memory":
        return InMemoryJobQueue()
    resolved_client = client or boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_url,
        config=Config(
            connect_timeout=settings.sqs_request_timeout_seconds,
            read_timeout=settings.sqs_request_timeout_seconds,
            retries={"max_attempts": 0},
        ),
    )
    return SQSJobQueue(resolved_client, settings.sqs_queue_url or "")
