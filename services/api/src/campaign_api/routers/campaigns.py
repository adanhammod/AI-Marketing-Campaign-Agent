from typing import Annotated
from uuid import UUID

from campaign_contracts.api import (
    ApprovalRequest,
    ApprovalResponse,
    CampaignCreationAcceptedResponse,
    CampaignCreationRequest,
    CampaignDetailResponse,
    CampaignListResponse,
    CancellationRequest,
    CancellationResponse,
    ConflictError,
    NotFoundError,
    RetryRequest,
    RetryResponse,
    RevisionRequest,
    RevisionResponse,
    StandardValidationError,
)
from fastapi import APIRouter, Depends, Header, Path, Query, Response, status

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


@router.post(
    "/{campaign_id}/versions/{version}/approve",
    response_model=ApprovalResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": NotFoundError},
        409: {"model": ConflictError},
        422: {"model": StandardValidationError},
        503: {"model": StandardValidationError},
    },
)
async def approve_campaign(
    campaign_id: UUID,
    version: Annotated[int, Path(ge=1)],
    body: ApprovalRequest,
    service: Annotated[CampaignService, Depends(get_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> ApprovalResponse:
    del idempotency_key  # required by the mutation contract; approval identity is the durable record itself
    return await service.approve(campaign_id, version, body, correlation_id())


@router.post(
    "/{campaign_id}/versions/{version}/cancel",
    response_model=CancellationResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"model": CancellationResponse},
        202: {"model": CancellationResponse},
        404: {"model": NotFoundError},
        409: {"model": ConflictError},
        422: {"model": StandardValidationError},
        503: {"model": StandardValidationError},
    },
)
async def cancel_campaign(
    campaign_id: UUID,
    version: Annotated[int, Path(ge=1)],
    body: CancellationRequest,
    response: Response,
    service: Annotated[CampaignService, Depends(get_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> CancellationResponse:
    del idempotency_key  # required by the mutation contract; cancellation identity is the durable record itself
    result = await service.cancel(campaign_id, version, body)
    if result.cancellation_pending:
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.post(
    "/{campaign_id}/versions/{version}/retry",
    response_model=RetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": NotFoundError},
        409: {"model": ConflictError},
        422: {"model": StandardValidationError},
        503: {"model": StandardValidationError},
    },
)
async def retry_campaign(
    campaign_id: UUID,
    version: Annotated[int, Path(ge=1)],
    body: RetryRequest,
    service: Annotated[CampaignService, Depends(get_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> RetryResponse:
    del idempotency_key  # required by the mutation contract; retry identity is the durable record itself
    return await service.retry(campaign_id, version, body, correlation_id())


@router.post(
    "/{campaign_id}/versions/{version}/revisions",
    response_model=RevisionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": NotFoundError},
        409: {"model": ConflictError},
        422: {"model": StandardValidationError},
        503: {"model": StandardValidationError},
    },
)
async def revise_campaign(
    campaign_id: UUID,
    version: Annotated[int, Path(ge=1)],
    body: RevisionRequest,
    service: Annotated[CampaignService, Depends(get_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> RevisionResponse:
    del idempotency_key  # required by the mutation contract; revision identity is the durable N+1 record itself
    return await service.revise(campaign_id, version, body, correlation_id())
