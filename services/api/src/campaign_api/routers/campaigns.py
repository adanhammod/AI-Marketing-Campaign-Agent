from typing import Annotated
from uuid import UUID

from campaign_contracts.api import (
    CampaignCreationAcceptedResponse,
    CampaignCreationRequest,
    CampaignDetailResponse,
    CampaignListResponse,
    ConflictError,
    NotFoundError,
    StandardValidationError,
)
from fastapi import APIRouter, Depends, Header, Query, status

from campaign_api.config import Settings
from campaign_api.dependencies import correlation_id, get_service, get_settings
from campaign_api.services.campaign_service import CampaignService

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post(
    "",
    response_model=CampaignCreationAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"model": ConflictError},
        422: {"model": StandardValidationError},
        503: {"model": StandardValidationError},
    },
)
async def create_campaign(
    body: CampaignCreationRequest,
    service: Annotated[CampaignService, Depends(get_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> CampaignCreationAcceptedResponse:
    return await service.create(body, correlation_id(), idempotency_key)


@router.get("", response_model=CampaignListResponse, responses={422: {"model": StandardValidationError}})
async def list_campaigns(
    service: Annotated[CampaignService, Depends(get_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> CampaignListResponse:
    return await service.list(offset, min(limit or 20, settings.max_page_size))


@router.get(
    "/{campaign_id}",
    response_model=CampaignDetailResponse,
    responses={404: {"model": NotFoundError}, 422: {"model": StandardValidationError}},
)
async def get_campaign(
    campaign_id: UUID, service: Annotated[CampaignService, Depends(get_service)]
) -> CampaignDetailResponse:
    return await service.get(campaign_id)
