import sys

import pytest

from campaign_worker.config import Settings
from campaign_worker.errors import ConfigurationError


def _settings(**overrides):
    defaults = dict(
        aws_region="us-east-1",
        queue_url="https://sqs.invalid/q",
        table_name="campaign-test",
        environment="test",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_settings_defaults_include_sqs_timeout_and_backoff_fields():
    settings = _settings()
    assert settings.sqs_connect_timeout_seconds == 10
    assert settings.sqs_read_timeout_seconds == 70
    assert settings.sqs_receive_retry_initial_backoff_seconds == 2
    assert settings.sqs_receive_retry_max_backoff_seconds == 30


def test_settings_from_env_reads_sqs_timeout_and_backoff_overrides(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    monkeypatch.setenv("AWS_SQS_CONNECT_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("AWS_SQS_READ_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("SQS_RECEIVE_RETRY_INITIAL_BACKOFF_SECONDS", "3")
    monkeypatch.setenv("SQS_RECEIVE_RETRY_MAX_BACKOFF_SECONDS", "45")

    settings = Settings.from_env()

    assert settings.sqs_connect_timeout_seconds == 15
    assert settings.sqs_read_timeout_seconds == 90
    assert settings.sqs_receive_retry_initial_backoff_seconds == 3
    assert settings.sqs_receive_retry_max_backoff_seconds == 45


def test_validate_rejects_read_timeout_too_close_to_wait_time():
    settings = _settings(wait_time_seconds=20, sqs_read_timeout_seconds=25)
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_validate_accepts_read_timeout_with_sufficient_headroom():
    settings = _settings(wait_time_seconds=20, sqs_read_timeout_seconds=30)
    settings.validate()


def test_validate_rejects_max_backoff_less_than_initial_backoff():
    settings = _settings(sqs_receive_retry_initial_backoff_seconds=10, sqs_receive_retry_max_backoff_seconds=5)
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_validate_rejects_non_positive_initial_backoff():
    settings = _settings(sqs_receive_retry_initial_backoff_seconds=0)
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_settings_defaults_include_polly_timeout_fields():
    settings = _settings()
    assert settings.polly_connect_timeout_seconds == 10
    assert settings.polly_read_timeout_seconds == 120


def test_settings_defaults_include_polly_retry_fields():
    settings = _settings()
    assert settings.polly_retry_mode == "standard"
    assert settings.polly_max_attempts == 2


def test_settings_from_env_reads_polly_timeout_overrides(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    monkeypatch.setenv("AWS_POLLY_CONNECT_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("AWS_POLLY_READ_TIMEOUT_SECONDS", "90")

    settings = Settings.from_env()

    assert settings.polly_connect_timeout_seconds == 12
    assert settings.polly_read_timeout_seconds == 90


def test_settings_from_env_reads_polly_retry_overrides(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    monkeypatch.setenv("AWS_POLLY_RETRY_MODE", "adaptive")
    monkeypatch.setenv("AWS_POLLY_MAX_ATTEMPTS", "3")

    settings = Settings.from_env()

    assert settings.polly_retry_mode == "adaptive"
    assert settings.polly_max_attempts == 3


def test_validate_rejects_non_positive_polly_timeouts():
    with pytest.raises(ConfigurationError):
        _settings(polly_connect_timeout_seconds=0).validate()
    with pytest.raises(ConfigurationError):
        _settings(polly_read_timeout_seconds=0).validate()


def test_validate_rejects_invalid_polly_retry_mode():
    with pytest.raises(ConfigurationError):
        _settings(polly_retry_mode="bogus").validate()


def test_validate_rejects_non_positive_polly_max_attempts():
    with pytest.raises(ConfigurationError):
        _settings(polly_max_attempts=0).validate()


def test_settings_defaults_video_renderer_mode_to_ffmpeg():
    assert _settings().video_renderer_mode == "ffmpeg"


def test_settings_from_env_reads_video_renderer_override(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    monkeypatch.setenv("VIDEO_RENDERER", "hyperframes")

    settings = Settings.from_env()

    assert settings.video_renderer_mode == "hyperframes"


def test_validate_video_pipeline_rejects_unsupported_renderer_mode():
    settings = _settings(ffmpeg_path=sys.executable, ffprobe_path=sys.executable, video_renderer_mode="bogus")
    with pytest.raises(ConfigurationError):
        settings.validate_video_pipeline()


def test_validate_video_pipeline_requires_npx_when_hyperframes_mode_selected():
    settings = _settings(
        ffmpeg_path=sys.executable,
        ffprobe_path=sys.executable,
        video_renderer_mode="hyperframes",
        npx_path="/nonexistent/npx-xyz",
    )
    with pytest.raises(ConfigurationError):
        settings.validate_video_pipeline()


def test_validate_video_pipeline_accepts_hyperframes_mode_when_npx_available():
    settings = _settings(
        ffmpeg_path=sys.executable,
        ffprobe_path=sys.executable,
        video_renderer_mode="hyperframes",
        npx_path=sys.executable,
    )
    settings.validate_video_pipeline()


def test_validate_video_pipeline_does_not_require_npx_in_ffmpeg_mode():
    settings = _settings(
        ffmpeg_path=sys.executable,
        ffprobe_path=sys.executable,
        video_renderer_mode="ffmpeg",
        npx_path="/nonexistent/npx-xyz",
    )
    settings.validate_video_pipeline()


def test_settings_defaults_cinematic_music_path_to_none():
    assert _settings().cinematic_music_path is None


def test_settings_from_env_reads_cinematic_music_path(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    monkeypatch.setenv("CINEMATIC_MUSIC_PATH", "/opt/assets/music/bed.wav")

    settings = Settings.from_env()

    assert settings.cinematic_music_path == "/opt/assets/music/bed.wav"


def test_settings_defaults_bedrock_creative_plan_model_id_to_none():
    assert _settings().bedrock_creative_plan_model_id is None


def test_settings_from_env_reads_bedrock_creative_plan_model_id(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    monkeypatch.setenv("BEDROCK_CREATIVE_PLAN_MODEL_ID", "anthropic.claude-3-haiku")

    settings = Settings.from_env()

    assert settings.bedrock_creative_plan_model_id == "anthropic.claude-3-haiku"


def test_settings_defaults_sfx_library_path_to_none():
    assert _settings().sfx_library_path is None


def test_settings_from_env_reads_sfx_library_path(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.invalid/q")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "campaign-test")
    monkeypatch.setenv("SFX_LIBRARY_PATH", "/opt/assets/sfx")

    settings = Settings.from_env()

    assert settings.sfx_library_path == "/opt/assets/sfx"
