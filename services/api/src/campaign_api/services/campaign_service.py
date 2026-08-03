from datetime import UTC, datetime
from uuid import UUID, uuid5

from campaign_contracts.api import (
    CampaignCreationAcceptedResponse,
    CampaignCreationRequest,
    CampaignDetailResponse,
    CampaignListResponse,
    CampaignSummary,
)
from campaign_contracts.campaign import (
    CampaignAggregateMetadata,
    CampaignConstraints,
    CampaignVersion,
    RetryMetadata,
)
from campaign_contracts.enums import CampaignStatus, SQSOperation
from campaign_contracts.sqs import SQSJobMessage

from campaign_api.exceptions import (
    CampaignNotFound,
    DuplicateCampaign,
    DuplicateJobConflict,
    QueueSubmissionAmbiguousFailure,
    QueueSubmissionFailure,
)
from campaign_api.queue.job_queue import JobQueue
from campaign_api.repositories.campaign_repository import CampaignRepository


class CampaignService:
    def __init__(self, repository: CampaignRepository, queue: JobQueue) -> None:
        self.repository = repository
        self.queue = queue

    async def create(
        self, request: CampaignCreationRequest, correlation_id: UUID, idempotency_key: str
    ) -> CampaignCreationAcceptedResponse:
        now = datetime.now(UTC)
        campaign_id = uuid5(UUID("f533fdad-f6b7-4d23-8d6d-7c68c77e8f53"), idempotency_key)
        job_id = uuid5(campaign_id, "START:1")
        title = f"{request.business_name}: {request.product_or_service}"
        aggregate = CampaignAggregateMetadata(
            campaign_id=campaign_id,
            current_version=1,
            title=title,
            created_at=now,
            updated_at=now,
            lock_version=0,
            event_sequence=0,
            current_status=CampaignStatus.CREATED,
            current_progress=0,
        )
        version = CampaignVersion(
            campaign_id=campaign_id,
            campaign_version=1,
            job_id=job_id,
            status=CampaignStatus.CREATED,
            current_step=None,
            progress_percent=0,
            brief=request,
            constraints=CampaignConstraints(),
            completed_steps=[],
            retry=RetryMetadata(),
            created_at=now,
            updated_at=now,
        )
        message = SQSJobMessage(
            schema_version=1,
            job_id=job_id,
            campaign_id=campaign_id,
            campaign_version=1,
            operation=SQSOperation.START,
            requested_step=None,
            revision_scope=None,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            requested_at=now,
            attempt=0,
            trace_id=correlation_id.hex,
        )
        try:
            await self.repository.create_initial(aggregate, version)
        except DuplicateCampaign:
            existing = await self.repository.get(campaign_id)
            if existing is None or existing[1].brief.model_dump(mode="json") != request.model_dump(mode="json"):
                raise DuplicateJobConflict("idempotency key reused with different request") from None
            if existing[1].status == CampaignStatus.QUEUED:
                return CampaignCreationAcceptedResponse(
                    campaign_id=campaign_id,
                    campaign_version=1,
                    job_id=existing[1].job_id,
                    status=CampaignStatus.QUEUED,
                    progress_percent=existing[1].progress_percent,
                    links={
                        "self": f"/api/v1/campaigns/{campaign_id}",
                        "events": f"/api/v1/campaigns/{campaign_id}/events",
                    },
                )
            raise QueueSubmissionAmbiguousFailure("campaign submission requires reconciliation") from None
        try:
            result = await self.queue.submit(message)
            if not result.accepted or result.job_id != job_id:
                raise QueueSubmissionFailure("queue submission failed")
        except QueueSubmissionAmbiguousFailure:
            # Preserve CREATED and the stable job_id: deleting could orphan an accepted message.
            raise
        except Exception:
            await self.repository.rollback_initial(campaign_id)
            raise
        queued_version = version.model_copy(
            update={
                "status": CampaignStatus.QUEUED,
                "progress_percent": 2,
                "updated_at": datetime.now(UTC),
                "lock_version": 1,
            }
        )
        queued_aggregate = aggregate.model_copy(
            update={
                "current_status": CampaignStatus.QUEUED,
                "current_progress": 2,
                "updated_at": queued_version.updated_at,
                "lock_version": 1,
            }
        )
        await self.repository.replace_current(queued_aggregate, queued_version)
        return CampaignCreationAcceptedResponse(
            campaign_id=campaign_id,
            campaign_version=1,
            job_id=job_id,
            status=CampaignStatus.QUEUED,
            progress_percent=2,
            links={"self": f"/api/v1/campaigns/{campaign_id}", "events": f"/api/v1/campaigns/{campaign_id}/events"},
        )

    @staticmethod
    def detail(a: CampaignAggregateMetadata, v: CampaignVersion) -> CampaignDetailResponse:
        return CampaignDetailResponse(
            campaign_id=v.campaign_id,
            campaign_version=v.campaign_version,
            status=v.status,
            current_step=v.current_step,
            progress_percent=v.progress_percent,
            brief=v.brief,
            strategy=v.strategy,
            copy=v.campaign_copy,
            storyboard=v.storyboard,
            artifacts=[],
            revision=v.revision,
            approval=v.approval,
            completed_steps=v.completed_steps,
            retry_eligible=v.status == CampaignStatus.FAILED and v.retry.retryable,
            error=v.error,
            created_at=v.created_at,
            updated_at=v.updated_at,
            title=a.title,
            current_version=a.current_version,
            latest_final_version=a.latest_final_version,
            event_sequence=a.event_sequence,
            actions={},
        )

    async def get(self, campaign_id: UUID) -> CampaignDetailResponse:
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        return self.detail(*record)

    async def list(self, offset: int, limit: int) -> CampaignListResponse:
        records = await self.repository.list(offset=offset, limit=limit + 1)
        has_more = len(records) > limit
        records = records[:limit]
        items = [
            CampaignSummary(
                campaign_id=a.campaign_id,
                title=a.title,
                current_version=a.current_version,
                latest_final_version=a.latest_final_version,
                status=v.status,
                current_step=v.current_step,
                progress_percent=v.progress_percent,
                created_at=a.created_at,
                updated_at=a.updated_at,
            )
            for a, v in records
        ]
        return CampaignListResponse(items=items, next_cursor=str(offset + limit) if has_more else None)
