import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from export_openapi import OUTPUT, build_schema  # noqa: E402


def test_build_schema_includes_the_live_routes():
    schema = build_schema()

    assert set(schema["paths"]) == {
        "/api/v1/campaigns",
        "/api/v1/campaigns/{campaign_id}",
        "/api/v1/campaigns/{campaign_id}/versions/{version}/approve",
        "/api/v1/campaigns/{campaign_id}/versions/{version}/cancel",
        "/api/v1/campaigns/{campaign_id}/versions/{version}/retry",
        "/health/live",
        "/health/ready",
    }


def test_build_schema_includes_generated_component_models():
    schema = build_schema()

    assert "CampaignCreationRequest" in schema["components"]["schemas"]
    assert "CampaignDetailResponse" in schema["components"]["schemas"]


def test_output_path_is_under_the_generated_contracts_directory():
    assert OUTPUT.parts[-3:] == ("contracts", "generated", "openapi.json")
