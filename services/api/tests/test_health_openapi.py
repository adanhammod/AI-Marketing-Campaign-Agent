from fastapi.testclient import TestClient

from campaign_api.dependencies import get_queue


class Unavailable:
    async def available(self):
        return False


def test_health(client):
    live = client.get("/health/live")
    ready = client.get("/health/ready")
    assert live.status_code == 200 and live.json()["status"] == "ok"
    assert ready.status_code == 200 and ready.json()["status"] == "ready"


def test_readiness_failure(app):
    app.dependency_overrides[get_queue] = lambda: Unavailable()
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503 and response.json()["status"] == "unavailable"


def test_openapi_scope_and_contracts(app):
    schema = app.openapi()
    paths = set(schema["paths"])
    assert {
        "/api/v1/campaigns",
        "/api/v1/campaigns/{campaign_id}",
        "/api/v1/campaigns/{campaign_id}/versions/{version}/approve",
        "/api/v1/campaigns/{campaign_id}/versions/{version}/cancel",
        "/health/live",
        "/health/ready",
    } == paths
    create = schema["paths"]["/api/v1/campaigns"]["post"]
    assert "202" in create["responses"]
    assert create["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/CampaignCreationRequest")
    forbidden = ("revision", "retry", "events", "artifacts")
    assert not any(any(word in path for word in forbidden) for path in paths)
    serialized = str(schema)
    assert "TypedLangGraphCampaignState" not in serialized and "ProcessingLease" not in serialized
