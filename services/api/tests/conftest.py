import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from campaign_api.config import Settings
from campaign_api.dependencies import get_queue, get_repository
from campaign_api.main import create_app
from campaign_api.queue.in_memory_job_queue import InMemoryJobQueue
from campaign_api.repositories.in_memory_campaign_repository import InMemoryCampaignRepository

ROOT = Path(__file__).parents[3]


@pytest.fixture
def repository():
    return InMemoryCampaignRepository()


@pytest.fixture
def queue():
    return InMemoryJobQueue()


@pytest.fixture
def app(repository, queue):
    application = create_app(Settings(max_page_size=10), repository, queue)
    application.dependency_overrides[get_repository] = lambda: repository
    application.dependency_overrides[get_queue] = lambda: queue
    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def valid_request():
    return json.loads((ROOT / "shared/fixtures/valid/api-create.json").read_text())


@pytest.fixture
def headers():
    return {"Idempotency-Key": "task6-test", "X-Request-ID": "018f0000-0000-7000-8000-000000000099"}
