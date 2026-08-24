import os
import shutil
from dataclasses import dataclass

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    aws_region: str | None = None
    queue_url: str | None = None
    artifact_bucket: str | None = None
    pexels_api_key: str | None = None
    bedrock_image_query_model_id: str | None = None
    # AI creative-plan generation (see graph/creative_plan_provider.py). Unset
    # means the deterministic generator is used directly -- Bedrock generation
    # is opt-in, and any Bedrock failure already falls back to it regardless.
    bedrock_creative_plan_model_id: str | None = None
    pexels_candidate_count: int = 15
    image_http_timeout_seconds: float = 20
    image_max_download_bytes: int = 25_000_000
    image_provider_mode: str = "generative"
    image_pexels_fallback_enabled: bool = True
    # Cloudflare Workers AI (FLUX) is the active generative image provider.
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_flux_model: str = "@cf/black-forest-labs/flux-1-schnell"
    cloudflare_flux_steps: int = 4
    # flux-1-schnell has no aspect-ratio request parameter -- this is
    # descriptive-only, used for cache-fingerprint/metadata parity with the
    # stock pipeline, never sent to Cloudflare's API.
    cloudflare_flux_aspect_ratio: str = "1:1"
    cloudflare_http_timeout_seconds: float = 30
    # Stability AI client/config are kept, unused, for rollback -- see
    # providers/stability_client.py. Not read by build_consumer() any more.
    stability_api_key: str | None = None
    stability_image_model: str = "sd3.5-large"
    stability_image_aspect_ratio: str = "9:16"
    stability_image_output_format: str = "jpeg"
    stability_http_timeout_seconds: float = 60
    polly_voice_id: str | None = None
    polly_engine: str = "neural"
    polly_connect_timeout_seconds: float = 10
    polly_read_timeout_seconds: float = 120
    polly_retry_mode: str = "standard"
    polly_max_attempts: int = 2
    audio_normalize_timeout_seconds: float = 30
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    video_render_timeout_seconds: float = 240
    video_max_download_bytes: int = 50_000_000
    video_renderer_mode: str = "ffmpeg"
    npx_path: str = "npx"
    # Local/dev fallback only for the CINEMATIC_TEXT_AD video style -- not the
    # production music-selection strategy (see video/audio_plan.py).
    cinematic_music_path: str | None = None
    # Local/dev SFX asset directory for video/audio_cue_library.py. Unset means
    # no SFX library configured -- every AudioCueType is skipped gracefully
    # rather than failing the render (SFX are optional, unlike music).
    sfx_library_path: str | None = None
    package_max_bytes: int = 52_428_800
    table_name: str | None = None
    wait_time_seconds: int = 20
    batch_size: int = 1
    visibility_timeout_seconds: int = 180
    heartbeat_interval_seconds: float = 60
    max_delivery_attempts: int = 4
    shutdown_grace_seconds: float = 30
    sqs_connect_timeout_seconds: float = 10
    sqs_read_timeout_seconds: float = 70
    sqs_receive_retry_initial_backoff_seconds: float = 2
    sqs_receive_retry_max_backoff_seconds: float = 30
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
        if self.sqs_connect_timeout_seconds <= 0 or self.sqs_read_timeout_seconds <= 0:
            raise ConfigurationError("SQS client timeouts must be positive")
        if self.sqs_read_timeout_seconds < self.wait_time_seconds + 10:
            raise ConfigurationError("SQS read timeout must exceed the wait time by at least 10 seconds")
        if self.sqs_receive_retry_initial_backoff_seconds <= 0 or self.sqs_receive_retry_max_backoff_seconds <= 0:
            raise ConfigurationError("SQS receive retry backoff bounds must be positive")
        if self.sqs_receive_retry_max_backoff_seconds < self.sqs_receive_retry_initial_backoff_seconds:
            raise ConfigurationError("SQS receive retry max backoff must be at least the initial backoff")
        if self.polly_connect_timeout_seconds <= 0 or self.polly_read_timeout_seconds <= 0:
            raise ConfigurationError("Polly client timeouts must be positive")
        if self.polly_retry_mode not in {"legacy", "standard", "adaptive"}:
            raise ConfigurationError("Polly retry mode must be one of legacy, standard, adaptive")
        if self.polly_max_attempts < 1:
            raise ConfigurationError("Polly max attempts must be at least 1")
        if self.endpoint_url and self.environment not in {"local", "test"}:
            raise ConfigurationError("endpoint URL is allowed only for local testing")
        if not 1 <= self.health_port <= 65535:
            raise ConfigurationError("health port must be between 1 and 65535")

    def validate_image_pipeline(self) -> None:
        if self.image_provider_mode not in {"generative", "stock"}:
            raise ConfigurationError("IMAGE_PROVIDER_MODE must be 'generative' or 'stock'")
        if not self.artifact_bucket or not self.bedrock_image_query_model_id:
            raise ConfigurationError("artifact bucket and Bedrock query model are required")
        needs_pexels = self.image_provider_mode == "stock" or self.image_pexels_fallback_enabled
        if needs_pexels and not self.pexels_api_key:
            raise ConfigurationError("Pexels API key is required for stock mode or when fallback is enabled")
        if needs_pexels and not 1 <= self.pexels_candidate_count <= 40:
            raise ConfigurationError("Pexels candidate count must be between 1 and 40")
        # Cloudflare is a hard dependency of generative mode -- fail fast at
        # startup rather than masking missing credentials as a per-job
        # "transient provider failure" once the pipeline starts falling back
        # to Pexels.
        if self.image_provider_mode == "generative":
            if not self.cloudflare_account_id or not self.cloudflare_api_token:
                raise ConfigurationError(
                    "Cloudflare account ID and API token are required when IMAGE_PROVIDER_MODE=generative"
                )
            if self.cloudflare_http_timeout_seconds <= 0:
                raise ConfigurationError("Cloudflare HTTP timeout must be positive")
        if self.image_http_timeout_seconds <= 0:
            raise ConfigurationError("image HTTP timeout must be positive")
        if self.image_max_download_bytes < 1:
            raise ConfigurationError("image download bound must be positive")

    def validate_voice_pipeline(self) -> None:
        if self.polly_engine not in {"standard", "neural", "long-form", "generative"}:
            raise ConfigurationError("Polly engine must be one of standard, neural, long-form, generative")
        if self.audio_normalize_timeout_seconds <= 0:
            raise ConfigurationError("audio normalize timeout must be positive")
        # Loudness normalization shells out to ffmpeg after Polly synthesis
        # (see audio/normalizer.py), so voiceover generation now hard-requires
        # ffmpeg on PATH -- fail fast at startup rather than surfacing this as
        # a confusing per-job runtime failure. Unlike validate_video_pipeline(),
        # there is no mock/degraded fallback mode for voice.
        if shutil.which(self.ffmpeg_path) is None:
            raise ConfigurationError(f"ffmpeg binary '{self.ffmpeg_path}' was not found on PATH")

    def validate_video_pipeline(self) -> None:
        if shutil.which(self.ffmpeg_path) is None:
            raise ConfigurationError(f"ffmpeg binary '{self.ffmpeg_path}' was not found on PATH")
        if shutil.which(self.ffprobe_path) is None:
            raise ConfigurationError(f"ffprobe binary '{self.ffprobe_path}' was not found on PATH")
        if self.video_render_timeout_seconds <= 0:
            raise ConfigurationError("video render timeout must be positive")
        if self.video_max_download_bytes < 1:
            raise ConfigurationError("video download bound must be positive")
        if self.video_renderer_mode not in {"ffmpeg", "hyperframes"}:
            raise ConfigurationError("VIDEO_RENDERER must be 'ffmpeg' or 'hyperframes'")
        # HyperFrames (heygen-com/hyperframes, run locally via `npx`) is opt-in
        # premium rendering -- fail fast at startup rather than only
        # discovering a missing npx/Node toolchain mid-render.
        if self.video_renderer_mode == "hyperframes" and shutil.which(self.npx_path) is None:
            raise ConfigurationError(
                f"npx binary '{self.npx_path}' was not found on PATH (required for VIDEO_RENDERER=hyperframes)"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        value = cls(
            aws_region=os.getenv("AWS_REGION"),
            queue_url=os.getenv("SQS_QUEUE_URL"),
            table_name=os.getenv("DYNAMODB_TABLE_NAME"),
            artifact_bucket=os.getenv("CAMPAIGN_ARTIFACT_BUCKET"),
            pexels_api_key=os.getenv("PEXELS_API_KEY"),
            bedrock_image_query_model_id=os.getenv("BEDROCK_IMAGE_QUERY_MODEL_ID"),
            bedrock_creative_plan_model_id=os.getenv("BEDROCK_CREATIVE_PLAN_MODEL_ID"),
            pexels_candidate_count=int(os.getenv("PEXELS_CANDIDATE_COUNT", "15")),
            image_http_timeout_seconds=float(os.getenv("IMAGE_HTTP_TIMEOUT_SECONDS", "20")),
            image_max_download_bytes=int(os.getenv("IMAGE_MAX_DOWNLOAD_BYTES", "25000000")),
            image_provider_mode=os.getenv("IMAGE_PROVIDER_MODE", "generative"),
            image_pexels_fallback_enabled=os.getenv("IMAGE_PEXELS_FALLBACK_ENABLED", "true").lower() == "true",
            cloudflare_account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID"),
            cloudflare_api_token=os.getenv("CLOUDFLARE_API_TOKEN"),
            cloudflare_flux_model=os.getenv("CLOUDFLARE_FLUX_MODEL", "@cf/black-forest-labs/flux-1-schnell"),
            cloudflare_flux_steps=int(os.getenv("CLOUDFLARE_FLUX_STEPS", "4")),
            cloudflare_flux_aspect_ratio=os.getenv("CLOUDFLARE_FLUX_ASPECT_RATIO", "1:1"),
            cloudflare_http_timeout_seconds=float(os.getenv("CLOUDFLARE_HTTP_TIMEOUT_SECONDS", "30")),
            stability_api_key=os.getenv("STABILITY_API_KEY"),
            stability_image_model=os.getenv("STABILITY_IMAGE_MODEL", "sd3.5-large"),
            stability_image_aspect_ratio=os.getenv("STABILITY_IMAGE_ASPECT_RATIO", "9:16"),
            stability_image_output_format=os.getenv("STABILITY_IMAGE_OUTPUT_FORMAT", "jpeg"),
            stability_http_timeout_seconds=float(os.getenv("STABILITY_HTTP_TIMEOUT_SECONDS", "60")),
            polly_voice_id=os.getenv("POLLY_VOICE_ID"),
            polly_engine=os.getenv("POLLY_ENGINE", "neural"),
            polly_connect_timeout_seconds=float(os.getenv("AWS_POLLY_CONNECT_TIMEOUT_SECONDS", "10")),
            polly_read_timeout_seconds=float(os.getenv("AWS_POLLY_READ_TIMEOUT_SECONDS", "120")),
            polly_retry_mode=os.getenv("AWS_POLLY_RETRY_MODE", "standard"),
            polly_max_attempts=int(os.getenv("AWS_POLLY_MAX_ATTEMPTS", "2")),
            audio_normalize_timeout_seconds=float(os.getenv("AUDIO_NORMALIZE_TIMEOUT_SECONDS", "30")),
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
            video_render_timeout_seconds=float(os.getenv("VIDEO_RENDER_TIMEOUT_SECONDS", "240")),
            video_max_download_bytes=int(os.getenv("VIDEO_MAX_DOWNLOAD_BYTES", "50000000")),
            video_renderer_mode=os.getenv("VIDEO_RENDERER", "ffmpeg"),
            npx_path=os.getenv("NPX_PATH", "npx"),
            cinematic_music_path=os.getenv("CINEMATIC_MUSIC_PATH"),
            sfx_library_path=os.getenv("SFX_LIBRARY_PATH"),
            package_max_bytes=int(os.getenv("PACKAGE_MAX_BYTES", "52428800")),
            wait_time_seconds=int(os.getenv("SQS_WAIT_TIME_SECONDS", "20")),
            batch_size=int(os.getenv("SQS_BATCH_SIZE", "1")),
            visibility_timeout_seconds=int(os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "180")),
            heartbeat_interval_seconds=float(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60")),
            max_delivery_attempts=int(os.getenv("WORKER_MAX_DELIVERY_ATTEMPTS", "4")),
            shutdown_grace_seconds=float(os.getenv("WORKER_SHUTDOWN_GRACE_SECONDS", "30")),
            sqs_connect_timeout_seconds=float(os.getenv("AWS_SQS_CONNECT_TIMEOUT_SECONDS", "10")),
            sqs_read_timeout_seconds=float(os.getenv("AWS_SQS_READ_TIMEOUT_SECONDS", "70")),
            sqs_receive_retry_initial_backoff_seconds=float(
                os.getenv("SQS_RECEIVE_RETRY_INITIAL_BACKOFF_SECONDS", "2")
            ),
            sqs_receive_retry_max_backoff_seconds=float(os.getenv("SQS_RECEIVE_RETRY_MAX_BACKOFF_SECONDS", "30")),
            endpoint_url=os.getenv("AWS_ENDPOINT_URL"),
            environment=os.getenv("ENVIRONMENT", "local"),
            service_name=os.getenv("WORKER_SERVICE_NAME", "campaign-worker"),
            health_port=int(os.getenv("WORKER_HEALTH_PORT", "8080")),
        )
        value.validate()
        return value
