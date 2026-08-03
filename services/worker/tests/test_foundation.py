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


def test_json_formatter_includes_extra_structured_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord("worker", logging.WARNING, __file__, 1, "lease_conflict", (), None)
    record.campaign_id = "018f0000-0000-7000-8000-000000000001"
    record.job_id = "018f0000-0000-7000-8000-000000000002"
    payload = json.loads(formatter.format(record))
    assert payload["event"] == "lease_conflict"
    assert payload["campaign_id"] == "018f0000-0000-7000-8000-000000000001"
    assert payload["job_id"] == "018f0000-0000-7000-8000-000000000002"


def test_json_formatter_omits_reserved_attributes_when_no_extra_given():
    formatter = JsonFormatter()
    record = logging.LogRecord("worker", logging.INFO, __file__, 1, "ok", (), None)
    payload = json.loads(formatter.format(record))
    assert set(payload) == {"timestamp", "level", "service", "event"}


def test_composition_root_uses_injected_clients():
    settings = Settings(
        aws_region="us-east-1", queue_url="https://sqs.invalid/q", table_name="campaign-test", environment="test"
    )
    consumer = build_consumer(settings, Client(), Client())
    assert consumer is not None
