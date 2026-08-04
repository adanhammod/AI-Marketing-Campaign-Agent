from campaign_worker.config import Settings
from campaign_worker.main import build_consumer
from campaign_worker.services.job_processor import GraphJobProcessor, NoOpJobProcessor


def _settings():
    return Settings(aws_region="us-east-1", queue_url="https://sqs.example/queue", table_name="campaign-table")


def test_build_consumer_wires_a_graph_job_processor():
    consumer = build_consumer(_settings(), sqs_client=object(), dynamodb_client=object())
    assert isinstance(consumer._processor, GraphJobProcessor)


def test_build_consumer_no_longer_constructs_a_no_op_job_processor():
    consumer = build_consumer(_settings(), sqs_client=object(), dynamodb_client=object())
    assert not isinstance(consumer._processor, NoOpJobProcessor)
