from fastapi.testclient import TestClient

from campaign_worker.config import Settings
from campaign_worker.health import build_health_app


class _FakeConsumer:
    def __init__(self, available: bool = True) -> None:
        self._available = available

    async def available(self) -> bool:
        return self._available


def _settings(**overrides) -> Settings:
    defaults: dict = {"service_name": "campaign-worker", "environment": "local"}
    defaults.update(overrides)
    return Settings(**defaults)


def test_health_live_returns_ok_without_checking_dependencies():
    app = build_health_app(_FakeConsumer(available=False), _settings())
    client = TestClient(app)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "campaign-worker", "environment": "local"}


def test_health_ready_returns_200_when_consumer_is_available():
    app = build_health_app(_FakeConsumer(available=True), _settings())
    client = TestClient(app)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_health_ready_returns_503_when_consumer_is_unavailable():
    app = build_health_app(_FakeConsumer(available=False), _settings())
    client = TestClient(app)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_metrics_endpoint_returns_prometheus_text_format():
    app = build_health_app(_FakeConsumer(), _settings())
    client = TestClient(app)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "python_gc_objects_collected_total" in response.text


def test_openapi_scope_is_limited_to_health_and_metrics():
    app = build_health_app(_FakeConsumer(), _settings())

    schema = app.openapi()

    assert set(schema["paths"]) == {"/health/live", "/health/ready", "/metrics"}


def test_health_responses_reflect_the_given_service_name_and_environment():
    app = build_health_app(_FakeConsumer(), _settings(service_name="custom-worker", environment="prod"))
    client = TestClient(app)

    response = client.get("/health/live")

    assert response.json() == {"status": "ok", "service": "custom-worker", "environment": "prod"}
