import pytest
from fastapi.testclient import TestClient

from campaign_worker.audio.pipeline import PollyVoicePipeline
from campaign_worker.config import Settings
from campaign_worker.errors import ConfigurationError
from campaign_worker.health import build_health_app
from campaign_worker.main import build_consumer
from campaign_worker.providers.mock_voice_provider import MockVoiceProvider
from campaign_worker.services.job_processor import GraphJobProcessor, NoOpJobProcessor


def _settings(**overrides):
    defaults = dict(
        aws_region="us-east-1",
        queue_url="https://sqs.example/queue",
        table_name="campaign-table",
        artifact_bucket="campaign-artifacts",
        pexels_api_key="test-key",
        bedrock_image_query_model_id="test-model",
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
