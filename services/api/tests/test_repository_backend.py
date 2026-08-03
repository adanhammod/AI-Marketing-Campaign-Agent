import pytest

from campaign_api.config import Settings
from campaign_api.repositories.dynamodb_campaign_repository import DynamoDBCampaignRepository
from campaign_api.repositories.factory import create_repository
from campaign_api.repositories.in_memory_campaign_repository import InMemoryCampaignRepository


def test_create_repository_defaults_to_memory():
    repository = create_repository(Settings())
    assert isinstance(repository, InMemoryCampaignRepository)


def test_create_repository_selects_dynamodb_with_injected_client():
    settings = Settings(repository_backend="dynamodb", dynamodb_table_name="campaign-agent-local")
    repository = create_repository(settings, client=object())
    assert isinstance(repository, DynamoDBCampaignRepository)


def test_create_repository_rejects_unknown_backend():
    with pytest.raises(ValueError, match="REPOSITORY_BACKEND"):
        create_repository(Settings(repository_backend="bogus"))


def test_settings_from_env_reads_repository_backend(monkeypatch):
    monkeypatch.setenv("REPOSITORY_BACKEND", "dynamodb")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://example.invalid/queue")
    settings = Settings.from_env()
    assert settings.repository_backend == "dynamodb"
