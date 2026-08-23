import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from campaign_worker.audio.pipeline import PollyVoicePipeline
from campaign_worker.config import Settings
from campaign_worker.errors import ConfigurationError
from campaign_worker.graph.creative_plan_provider import (
    BedrockCreativePlanProvider,
    DeterministicCreativePlanProvider,
    FallbackCreativePlanProvider,
)
from campaign_worker.health import build_health_app
from campaign_worker.images.generative_pipeline import GenerativeImagePipeline
from campaign_worker.images.pipeline import StockImagePipeline
from campaign_worker.main import build_consumer
from campaign_worker.package.pipeline import S3PackagePipeline
from campaign_worker.providers.mock_package_pipeline import MockPackagePipeline
from campaign_worker.providers.mock_video_provider import MockVideoProvider
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.services.job_processor import GraphJobProcessor, NoOpJobProcessor
from campaign_worker.video.ffmpeg_renderer import FfmpegVideoRenderer
from campaign_worker.video.hyperframes_renderer import HyperFramesVideoRenderer
from campaign_worker.video.pipeline import FfmpegVideoPipeline


def _settings(**overrides):
    defaults = dict(
        aws_region="us-east-1",
        queue_url="https://sqs.example/queue",
        table_name="campaign-table",
        artifact_bucket="campaign-artifacts",
        pexels_api_key="test-key",
        bedrock_image_query_model_id="test-model",
        cloudflare_account_id="test-account-id",
        cloudflare_api_token="test-cloudflare-token",
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


def test_build_consumer_rejects_all_mock_fallback_outside_local_and_test():
    """A deployed worker (dev/prod) whose real-pipeline config is entirely missing --
    e.g. campaign-secrets was never created -- must fail fast at startup instead of
    silently running every campaign through mock providers with no signal that anything
    is wrong."""
    settings = _settings(
        artifact_bucket=None,
        pexels_api_key=None,
        bedrock_image_query_model_id=None,
        cloudflare_account_id=None,
        cloudflare_api_token=None,
        environment="dev",
    )
    with pytest.raises(ConfigurationError, match="no image/voice pipeline is configured"):
        build_consumer(settings, sqs_client=object(), dynamodb_client=object())


def test_build_consumer_allows_all_mock_fallback_in_local_and_test():
    for environment in ("local", "test"):
        settings = _settings(
            artifact_bucket=None,
            pexels_api_key=None,
            bedrock_image_query_model_id=None,
            cloudflare_account_id=None,
            cloudflare_api_token=None,
            environment=environment,
        )
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


def test_build_consumer_defaults_to_the_ffmpeg_renderer():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    video_provider = consumer._processor._video_provider
    assert isinstance(video_provider, FfmpegVideoPipeline)
    assert isinstance(video_provider._renderer, FfmpegVideoRenderer)


def test_build_consumer_wires_the_hyperframes_renderer_when_video_renderer_is_hyperframes():
    settings = _settings(video_renderer_mode="hyperframes", npx_path=sys.executable)
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    video_provider = consumer._processor._video_provider
    assert isinstance(video_provider, FfmpegVideoPipeline)
    assert isinstance(video_provider._renderer, HyperFramesVideoRenderer)


def test_build_consumer_wires_cinematic_music_path_into_the_video_pipeline():
    settings = _settings(cinematic_music_path="/opt/assets/music/bed.wav")
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    video_provider = consumer._processor._video_provider
    assert isinstance(video_provider, FfmpegVideoPipeline)
    assert video_provider._music_path == Path("/opt/assets/music/bed.wav")


def test_build_consumer_defaults_music_path_to_none_when_unset():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert consumer._processor._video_provider._music_path is None


def test_build_consumer_wires_sfx_library_path_into_the_video_pipeline():
    settings = _settings(sfx_library_path="/opt/assets/sfx")
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    video_provider = consumer._processor._video_provider
    assert isinstance(video_provider, FfmpegVideoPipeline)
    assert video_provider._sfx_library_root == Path("/opt/assets/sfx")


def test_build_consumer_defaults_sfx_library_root_to_none_when_unset():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert consumer._processor._video_provider._sfx_library_root is None


def test_build_consumer_defaults_creative_plan_provider_to_deterministic_when_unset():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert isinstance(consumer._processor._creative_plan_provider, DeterministicCreativePlanProvider)


def test_build_consumer_wires_bedrock_creative_plan_provider_with_fallback_when_configured():
    consumer = build_consumer(
        _settings(bedrock_creative_plan_model_id="test-creative-plan-model"),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    provider = consumer._processor._creative_plan_provider
    assert isinstance(provider, FallbackCreativePlanProvider)
    assert isinstance(provider._primary, BedrockCreativePlanProvider)
    assert provider._primary._model_id == "test-creative-plan-model"
    assert isinstance(provider._fallback, DeterministicCreativePlanProvider)


def test_build_consumer_wires_creative_plan_provider_even_in_all_mock_branch():
    # bedrock_image_query_model_id/pexels/cloudflare all unset -> the big
    # if/else in build_consumer takes the all-mock branch for image/voice/
    # video, but creative-plan generation is an independent gate.
    consumer = build_consumer(
        Settings(
            aws_region="us-east-1",
            queue_url="https://sqs.example/queue",
            table_name="campaign-table",
            bedrock_creative_plan_model_id="test-creative-plan-model",
        ),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
    )
    provider = consumer._processor._creative_plan_provider
    assert isinstance(provider, FallbackCreativePlanProvider)
    assert isinstance(provider._primary, BedrockCreativePlanProvider)


def test_build_consumer_falls_back_to_mock_video_provider_when_hyperframes_selected_but_npx_missing():
    settings = _settings(video_renderer_mode="hyperframes", npx_path="/nonexistent/npx-xyz")
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
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


def test_build_consumer_defaults_image_provider_mode_to_generative_and_wires_generative_pipeline():
    consumer = build_consumer(
        _settings(),
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert isinstance(consumer._processor._image_provider, GenerativeImagePipeline)


def test_build_consumer_in_stock_mode_wires_stock_pipeline_only_and_never_constructs_cloudflare_client():
    settings = _settings(image_provider_mode="stock", cloudflare_account_id=None, cloudflare_api_token=None)
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
        polly_client=object(),
    )
    assert isinstance(consumer._processor._image_provider, StockImagePipeline)


def test_build_consumer_raises_configuration_error_when_generative_mode_missing_cloudflare_credentials():
    settings = _settings(cloudflare_account_id=None, cloudflare_api_token=None)
    with pytest.raises(ConfigurationError):
        build_consumer(
            settings,
            sqs_client=object(),
            dynamodb_client=object(),
            bedrock_client=object(),
            s3_client=object(),
            polly_client=object(),
        )


def test_build_consumer_raises_configuration_error_when_stock_mode_missing_pexels_key():
    settings = _settings(image_provider_mode="stock", pexels_api_key=None)
    with pytest.raises(ConfigurationError):
        build_consumer(
            settings,
            sqs_client=object(),
            dynamodb_client=object(),
            bedrock_client=object(),
            s3_client=object(),
            polly_client=object(),
        )


def test_build_consumer_constructs_sqs_client_with_its_own_configured_timeouts():
    # No sqs_client injected, so build_consumer must construct a real boto3 SQS client
    # (a local, non-network operation) using the SQS-specific timeout settings, isolated
    # from the shared Config used for DynamoDB/Bedrock/S3/Polly.
    settings = _settings(
        artifact_bucket=None,
        pexels_api_key=None,
        bedrock_image_query_model_id=None,
        sqs_connect_timeout_seconds=15,
        sqs_read_timeout_seconds=90,
    )
    consumer = build_consumer(settings, dynamodb_client=object())
    client_config = consumer._client.meta.config
    assert client_config.connect_timeout == 15
    assert client_config.read_timeout == 90


def test_build_consumer_constructs_polly_client_with_its_own_configured_timeouts():
    # No polly_client injected, so build_consumer must construct a real boto3 Polly
    # client (a local, non-network operation) using the Polly-specific timeout
    # settings, isolated from the shared Config used for DynamoDB/Bedrock/S3.
    settings = _settings(polly_connect_timeout_seconds=12, polly_read_timeout_seconds=90)
    consumer = build_consumer(
        settings,
        sqs_client=object(),
        dynamodb_client=object(),
        bedrock_client=object(),
        s3_client=object(),
    )
    client_config = consumer._processor._voice_provider._client.meta.config
    assert client_config.connect_timeout == 12
    assert client_config.read_timeout == 90
