from contextvars import ContextVar
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import Depends, Request

from campaign_api.config import Settings
from campaign_api.queue.job_queue import JobQueue
from campaign_api.repositories.campaign_repository import CampaignRepository
from campaign_api.services.campaign_service import CampaignService

correlation_id_var: ContextVar[UUID | None] = ContextVar("correlation_id", default=None)


def get_repository(request: Request) -> CampaignRepository:
    return cast(CampaignRepository, request.app.state.repository)


def get_queue(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.queue)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_service(
    repository: Annotated[CampaignRepository, Depends(get_repository)], queue: Annotated[JobQueue, Depends(get_queue)]
) -> CampaignService:
    return CampaignService(repository, queue)


def correlation_id() -> UUID:
    return correlation_id_var.get() or uuid4()
