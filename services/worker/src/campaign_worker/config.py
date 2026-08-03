import os
from dataclasses import dataclass

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    aws_region: str | None = None
    queue_url: str | None = None
    table_name: str | None = None
    wait_time_seconds: int = 20
    batch_size: int = 1
    visibility_timeout_seconds: int = 180
    heartbeat_interval_seconds: float = 60
    max_delivery_attempts: int = 4
    shutdown_grace_seconds: float = 30
    endpoint_url: str | None = None
    environment: str = "local"

    def validate(self) -> None:
        if not self.aws_region or not self.queue_url or not self.table_name:
            raise ConfigurationError("AWS region, queue URL, and table name are required")
        if not 0 <= self.wait_time_seconds <= 20:
            raise ConfigurationError("wait time must be between 0 and 20 seconds")
        if not 1 <= self.batch_size <= 10:
            raise ConfigurationError("batch size must be between 1 and 10")
        if self.heartbeat_interval_seconds <= 0 or self.visibility_timeout_seconds <= self.heartbeat_interval_seconds:
            raise ConfigurationError("visibility timeout must exceed the heartbeat interval")
        if self.max_delivery_attempts < 1 or self.shutdown_grace_seconds <= 0:
            raise ConfigurationError("retry and shutdown bounds must be positive")
        if self.endpoint_url and self.environment not in {"local", "test"}:
            raise ConfigurationError("endpoint URL is allowed only for local testing")

    @classmethod
    def from_env(cls) -> "Settings":
        value = cls(
            aws_region=os.getenv("AWS_REGION"),
            queue_url=os.getenv("SQS_QUEUE_URL"),
            table_name=os.getenv("DYNAMODB_TABLE_NAME"),
            wait_time_seconds=int(os.getenv("SQS_WAIT_TIME_SECONDS", "20")),
            batch_size=int(os.getenv("SQS_BATCH_SIZE", "1")),
            visibility_timeout_seconds=int(os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "180")),
            heartbeat_interval_seconds=float(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60")),
            max_delivery_attempts=int(os.getenv("WORKER_MAX_DELIVERY_ATTEMPTS", "4")),
            shutdown_grace_seconds=float(os.getenv("WORKER_SHUTDOWN_GRACE_SECONDS", "30")),
            endpoint_url=os.getenv("AWS_ENDPOINT_URL"),
            environment=os.getenv("ENVIRONMENT", "local"),
        )
        value.validate()
        return value
