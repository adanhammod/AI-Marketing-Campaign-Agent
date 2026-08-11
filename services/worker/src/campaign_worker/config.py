import os
from dataclasses import dataclass

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    aws_region: str | None = None
    queue_url: str | None = None
    artifact_bucket: str | None = None
    pexels_api_key: str | None = None
    bedrock_image_query_model_id: str | None = None
    pexels_candidate_count: int = 15
    image_http_timeout_seconds: float = 20
    image_max_download_bytes: int = 25_000_000
    table_name: str | None = None
    wait_time_seconds: int = 20
    batch_size: int = 1
    visibility_timeout_seconds: int = 180
    heartbeat_interval_seconds: float = 60
    max_delivery_attempts: int = 4
    shutdown_grace_seconds: float = 30
    endpoint_url: str | None = None
    environment: str = "local"
    service_name: str = "campaign-worker"
    health_port: int = 8080

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
        if not 1 <= self.health_port <= 65535:
            raise ConfigurationError("health port must be between 1 and 65535")

    def validate_image_pipeline(self) -> None:
        if not self.artifact_bucket or not self.pexels_api_key or not self.bedrock_image_query_model_id:
            raise ConfigurationError("artifact bucket, Pexels API key, and Bedrock query model are required")
        if not 1 <= self.pexels_candidate_count <= 40:
            raise ConfigurationError("Pexels candidate count must be between 1 and 40")
        if self.image_http_timeout_seconds <= 0:
            raise ConfigurationError("image HTTP timeout must be positive")
        if self.image_max_download_bytes < 1:
            raise ConfigurationError("image download bound must be positive")

    @classmethod
    def from_env(cls) -> "Settings":
        value = cls(
            aws_region=os.getenv("AWS_REGION"),
            queue_url=os.getenv("SQS_QUEUE_URL"),
            table_name=os.getenv("DYNAMODB_TABLE_NAME"),
            artifact_bucket=os.getenv("CAMPAIGN_ARTIFACT_BUCKET"),
            pexels_api_key=os.getenv("PEXELS_API_KEY"),
            bedrock_image_query_model_id=os.getenv("BEDROCK_IMAGE_QUERY_MODEL_ID"),
            pexels_candidate_count=int(os.getenv("PEXELS_CANDIDATE_COUNT", "15")),
            image_http_timeout_seconds=float(os.getenv("IMAGE_HTTP_TIMEOUT_SECONDS", "20")),
            image_max_download_bytes=int(os.getenv("IMAGE_MAX_DOWNLOAD_BYTES", "25000000")),
            wait_time_seconds=int(os.getenv("SQS_WAIT_TIME_SECONDS", "20")),
            batch_size=int(os.getenv("SQS_BATCH_SIZE", "1")),
            visibility_timeout_seconds=int(os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "180")),
            heartbeat_interval_seconds=float(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60")),
            max_delivery_attempts=int(os.getenv("WORKER_MAX_DELIVERY_ATTEMPTS", "4")),
            shutdown_grace_seconds=float(os.getenv("WORKER_SHUTDOWN_GRACE_SECONDS", "30")),
            endpoint_url=os.getenv("AWS_ENDPOINT_URL"),
            environment=os.getenv("ENVIRONMENT", "local"),
            service_name=os.getenv("WORKER_SERVICE_NAME", "campaign-worker"),
            health_port=int(os.getenv("WORKER_HEALTH_PORT", "8080")),
        )
        value.validate()
        return value
        if value.environment != "test":
            value.validate_image_pipeline()
