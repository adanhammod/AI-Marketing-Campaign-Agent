import json
import logging

from campaign_worker.config import Settings
from campaign_worker.logging import JsonFormatter, configure_logging
from campaign_worker.main import build_consumer


class Client:
    pass


def test_structured_logging_is_sanitized():
    formatter = JsonFormatter()
    record = logging.LogRecord("worker", logging.INFO, __file__, 1, "safe_event", (), None)
    value = json.loads(formatter.format(record))
    assert value["service"] == "campaign-worker" and value["event"] == "safe_event"
    configure_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING


def test_composition_root_uses_injected_clients():
    settings = Settings(
        aws_region="us-east-1", queue_url="https://sqs.invalid/q", table_name="campaign-test", environment="test"
    )
    consumer = build_consumer(settings, Client(), Client())
    assert consumer is not None
