import sys

import pytest
from fastapi.testclient import TestClient

from campaign_worker.audio.pipeline import PollyVoicePipeline
from campaign_worker.config import Settings
from campaign_worker.errors import ConfigurationError
from campaign_worker.health import build_health_app
from campaign_worker.main import build_consumer
from campaign_worker.package.pipeline import S3PackagePipeline
from campaign_worker.providers.mock_package_pipeline import MockPackagePipeline
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.services.job_processor import GraphJobProcessor, NoOpJobProcessor
from campaign_worker.video.pipeline import FfmpegVideoPipeline


def _settings(**overrides):
    defaults = dict(
        aws_region="us-east-1",
        queue_url="https://sqs.example/queue",
        table_name="campaign-table",
        artifact_bucket="campaign-artifacts",
        pexels_api_key="test-key",
        bedrock_image_query_model_id="test-model",
        # sys.executable is guaranteed to exist and be executable in any test
        # environment, standing in for a real ffmpeg/ffprobe binary purely
        # for availability detection. The voice pipeline now hard-requires
        # ffmpeg too (loudness normalization), so this must default to an
        # available path for the many tests below that expect a fully-wired
        # real (non-mock) asset pipeline to construct successfully.
        ffmpeg_path=sys.executable,
        ffprobe_path=sys.executable,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_build_consumer_wires_a_graph_job_processor():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
    )
    assert isinstance(consumer._processor, GraphJobProcessor)


def test_build_consumer_no_longer_constructs_a_no_op_job_processor():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
    )
    assert not isinstance(consumer._processor, NoOpJobProcessor)


def test_build_consumer_output_wires_correctly_into_the_health_app():
    settings = _settings()
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
    )
    app = build_health_app(consumer, settings)

    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json()["service"] == "campaign-worker"


def test_build_consumer_wires_a_real_polly_voice_pipeline_when_the_asset_pipeline_is_configured():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert isinstance(consumer._processor._voice_provider, PollyVoicePipeline)


def test_build_consumer_falls_back_to_mock_voice_provider_when_asset_pipeline_is_not_configured():
    settings = _settings(artifact_bucket=None, pexels_api_key=None, bedrock_image_query_model_id=None)
    consumer = build_consumer(settings, sqs_client=object(), dynamodb_client=object())
    assert isinstance(consumer._processor._voice_provider, MockVoiceProvider)


def test_build_consumer_rejects_an_unsupported_polly_engine():
    settings = _settings(polly_engine="ultra-fast")
    with pytest.raises(ConfigurationError):
        build_consumer(
            settings,
            sqs_client=object(),
            dynamodb_client=object(),
            bedrock_client=object(),
            s3_client=object(),
            polly_client=object(),
        )


def test_build_consumer_wires_a_real_ffmpeg_video_pipeline_when_ffmpeg_is_available():
    # sys.executable is guaranteed to exist and be executable in any test environment,
    # standing in for a real ffmpeg/ffprobe binary purely for availability detection.
    settings = _settings(ffmpeg_path=sys.executable, ffprobe_path=sys.executable)
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert isinstance(consumer._processor._video_provider, FfmpegVideoPipeline)


def test_build_consumer_raises_when_ffmpeg_is_unavailable_for_the_voice_pipeline():
    # ffmpeg/ffprobe paths are shared by both pipelines, and loudness
    # normalization (audio/normalizer.py) now makes ffmpeg a hard dependency
    # of voiceover generation too. Unlike video's MockVideoProvider fallback,
    # there is no degraded mode for voice -- so when ffmpeg is unavailable,
    # build_consumer fails fast at startup (validate_voice_pipeline) instead
    # of silently pairing a real voiceover with a fake/mock video.
    settings = _settings(ffmpeg_path="/nonexistent/ffmpeg-xyz", ffprobe_path="/nonexistent/ffprobe-xyz")
    with pytest.raises(ConfigurationError):
        build_consumer(
            settings,
            sqs_client=object(),
            dynamodb_client=object(),
            bedrock_client=object(),
            s3_client=object(),
            polly_client=object(),
        )


def test_build_consumer_falls_back_to_mock_video_provider_when_asset_pipeline_is_not_configured():
    settings = _settings(artifact_bucket=None, pexels_api_key=None, bedrock_image_query_model_id=None)
    consumer = build_consumer(settings, sqs_client=object(), dynamodb_client=object())
    assert isinstance(consumer._processor._video_provider, MockVideoProvider)


def test_build_consumer_wires_a_real_s3_package_pipeline_when_the_asset_pipeline_is_configured():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert isinstance(consumer._processor._package_pipeline, S3PackagePipeline)


def test_build_consumer_falls_back_to_mock_package_pipeline_when_asset_pipeline_is_not_configured():
    settings = _settings(artifact_bucket=None, pexels_api_key=None, bedrock_image_query_model_id=None)
    consumer = build_consumer(settings, sqs_client=object(), dynamodb_client=object())
    assert isinstance(consumer._processor._package_pipeline, MockPackagePipeline)
