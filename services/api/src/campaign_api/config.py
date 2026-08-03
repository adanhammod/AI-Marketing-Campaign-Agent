import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    service_name: str = "campaign-api"
    environment: str = "local"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    max_page_size: int = 100
    dynamodb_table_name: str = "campaign-agent-local"
    queue_backend: str = "memory"
    aws_region: str | None = None
    sqs_queue_url: str | None = None
    sqs_request_timeout_seconds: float = 10.0
    sqs_endpoint_url: str | None = None

    def validate(self) -> None:
        if self.queue_backend not in {"memory", "sqs"}:
            raise ValueError("QUEUE_BACKEND must be memory or sqs")
        if self.sqs_request_timeout_seconds <= 0:
            raise ValueError("SQS_REQUEST_TIMEOUT_SECONDS must be positive")
        if self.queue_backend == "sqs" and (not self.aws_region or not self.sqs_queue_url):
            raise ValueError("AWS_REGION and SQS_QUEUE_URL are required when QUEUE_BACKEND=sqs")
        if self.sqs_endpoint_url and self.environment not in {"local", "test"}:
            raise ValueError("SQS_ENDPOINT_URL is allowed only for local testing")

    @classmethod
    def from_env(cls) -> "Settings":
        size = int(os.getenv("MAX_PAGE_SIZE", "100"))
        if not 1 <= size <= 100:
            raise ValueError("MAX_PAGE_SIZE must be between 1 and 100")
        prefix = os.getenv("API_PREFIX", "/api/v1")
        if not prefix.startswith("/"):
            raise ValueError("API_PREFIX must start with /")
        settings = cls(
            service_name=os.getenv("SERVICE_NAME", "campaign-api"),
            environment=os.getenv("ENVIRONMENT", "local"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            api_prefix=prefix.rstrip("/"),
            max_page_size=size,
            dynamodb_table_name=os.getenv("DYNAMODB_TABLE_NAME", "campaign-agent-local"),
            queue_backend=os.getenv("QUEUE_BACKEND", "memory").lower(),
            aws_region=os.getenv("AWS_REGION"),
            sqs_queue_url=os.getenv("SQS_QUEUE_URL"),
            sqs_request_timeout_seconds=float(os.getenv("SQS_REQUEST_TIMEOUT_SECONDS", "10")),
            sqs_endpoint_url=os.getenv("SQS_ENDPOINT_URL"),
        )
        settings.validate()
        return settings
