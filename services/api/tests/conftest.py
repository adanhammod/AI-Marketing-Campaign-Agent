import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from campaign_contracts.campaign import (
    CampaignAggregateMetadata,
    CampaignConstraints,
    CampaignVersion,
    NormalizedCampaignBrief,
    RetryMetadata,
    ReviewPackage,
)
from campaign_contracts.enums import CampaignStatus
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


@pytest.fixture
def approval_checksum():
    return "a" * 64


@pytest.fixture
def ready_for_review_campaign(repository, approval_checksum):
    """Returns an async factory that persists a READY_FOR_REVIEW campaign with a matching
    review_package and returns (campaign_id, original_start_job_id)."""

    async def _create(checksum: str | None = None) -> tuple:
        campaign_id = uuid4()
        job_id = uuid4()
        now = datetime.now(UTC)
        brief = NormalizedCampaignBrief.model_validate(
            json.loads((ROOT / "shared/fixtures/valid/api-create.json").read_text())
        )
        aggregate = CampaignAggregateMetadata(
            campaign_id=campaign_id,
            current_version=1,
            title="Test Campaign",
            created_at=now,
            updated_at=now,
            lock_version=0,
            event_sequence=0,
            current_status=CampaignStatus.READY_FOR_REVIEW,
            current_progress=95,
        )
        version = CampaignVersion(
            campaign_id=campaign_id,
            campaign_version=1,
            job_id=job_id,
            status=CampaignStatus.READY_FOR_REVIEW,
            current_step=None,
            progress_percent=95,
            brief=brief,
            constraints=CampaignConstraints(),
            review_package=ReviewPackage(
                artifact_id=uuid4(), manifest_checksum=checksum or approval_checksum, artifact_ids=[uuid4()]
            ),
            completed_steps=[],
            retry=RetryMetadata(),
            created_at=now,
            updated_at=now,
        )
        await repository.create_initial(aggregate, version)
        return campaign_id, job_id

    return _create
