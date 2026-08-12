from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from campaign_contracts.api import (
    ApprovalRequest,
    ApprovalResponse,
    CampaignArtifactsResponse,
    CampaignCreationAcceptedResponse,
    CampaignCreationRequest,
    CampaignDetailResponse,
    CampaignEventsResponse,
    CampaignListResponse,
    CampaignSummary,
    CancellationRequest,
    CancellationResponse,
    RetryRequest,
    RetryResponse,
    RevisionRequest,
    RevisionResponse,
)
from campaign_contracts.artifacts import PublicArtifactReference
from campaign_contracts.campaign import (
    ApprovalRecord,
    CampaignAggregateMetadata,
    CampaignConstraints,
    CampaignVersion,
    RetryMetadata,
    RevisionFeedback,
    earliest_regeneration_step,
    validate_approval_target,
)
from campaign_contracts.enums import Actor, ArtifactType, CampaignEventType, CampaignStatus, SQSOperation, WorkflowStep
from campaign_contracts.events import CampaignEvent
from campaign_contracts.sqs import SQSJobMessage
from campaign_contracts.validation import is_terminal, validate_transition

from campaign_api.artifacts.artifact_url_signer import ArtifactURLSigner
from campaign_api.exceptions import (
    CampaignNotFound,
    DuplicateCampaign,
    DuplicateJobConflict,
    InvalidCursor,
    InvalidStateTransition,
    QueueSubmissionAmbiguousFailure,
    QueueSubmissionFailure,
    RepositoryFailure,
)
from campaign_api.queue.job_queue import JobQueue
from campaign_api.repositories.campaign_repository import CampaignRepository

_CANCELLATION_PENDING_STATUSES = frozenset(
    {
        CampaignStatus.GENERATING_STRATEGY,
        CampaignStatus.GENERATING_COPY,
        CampaignStatus.GENERATING_STORYBOARD,
        CampaignStatus.GENERATING_IMAGES,
        CampaignStatus.RENDERING_VIDEO,
    }
)

# Pipeline order used only to decide which content fields a revision reuses from its
# parent: a field is reused iff its step comes strictly before earliest_affected_step.
_STEP_ORDER = (
    WorkflowStep.STRATEGY,
    WorkflowStep.COPY,
    WorkflowStep.STORYBOARD,
    WorkflowStep.IMAGES,
    WorkflowStep.VOICEOVER,
    WorkflowStep.VIDEO,
)

# Progress floor a freshly-created revision child starts at. Only COPY's floor (20) is
# confirmed by the shared revision-version-2.json fixture; the rest are a reasonable,
# self-consistent interpolation, not independently contract-pinned.
_REVISION_PROGRESS_FLOOR: dict[WorkflowStep, int] = {
    WorkflowStep.STRATEGY: 2,
    WorkflowStep.COPY: 20,
    WorkflowStep.STORYBOARD: 40,
    WorkflowStep.IMAGES: 60,
    WorkflowStep.VOICEOVER: 70,
    WorkflowStep.VIDEO: 80,
}


class CampaignService:
    def __init__(
        self,
        repository: CampaignRepository,
        queue: JobQueue,
        artifact_signer: ArtifactURLSigner | None = None,
    ) -> None:
        self.repository = repository
        self.queue = queue

        self.artifact_signer = artifact_signer

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
            event_sequence=1,
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
        created_event = CampaignEvent(
            event_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=1,
            event_sequence=1,
            event_type=CampaignEventType.CAMPAIGN_CREATED,
            status=CampaignStatus.CREATED,
            step=None,
            progress_percent=0,
            occurred_at=now,
            actor=Actor.FASTAPI,
            correlation_id=correlation_id,
            job_id=job_id,
            details={},
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
            await self.repository.create_initial(aggregate, version, [created_event])
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
            await self.repository.rollback_initial(campaign_id, [created_event])
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

    async def approve(
        self, campaign_id: UUID, campaign_version: int, request: ApprovalRequest, correlation_id: UUID
    ) -> ApprovalResponse:
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        aggregate, current = record
        if current.campaign_version != campaign_version:
            raise InvalidStateTransition("approval must target the current campaign version")

        existing_approval = current.approval
        approval: ApprovalRecord
        if current.status == CampaignStatus.APPROVED and existing_approval is not None:
            if (
                existing_approval.manifest_checksum != request.review_manifest_checksum
                or existing_approval.note != request.note
            ):
                raise DuplicateJobConflict("approval already recorded with a different request")
            approval = existing_approval
        else:
            try:
                validate_approval_target(aggregate, current)
            except ValueError as exc:
                raise InvalidStateTransition(str(exc)) from None
            if (
                current.review_package is None
                or current.review_package.manifest_checksum != request.review_manifest_checksum
            ):
                raise InvalidStateTransition("review manifest checksum does not match")
            now = datetime.now(UTC)
            resume_job_id = uuid5(current.campaign_id, f"RESUME:{current.campaign_version}")
            approval = ApprovalRecord(
                approval_id=uuid4(),
                campaign_id=current.campaign_id,
                campaign_version=current.campaign_version,
                approved_at=now,
                manifest_checksum=request.review_manifest_checksum,
                note=request.note,
                created_at=now,
            )
            updated_version = current.model_copy(
                update={
                    "status": CampaignStatus.APPROVED,
                    "job_id": resume_job_id,
                    "approval": approval,
                    "progress_percent": 98,
                    "updated_at": now,
                    "lock_version": current.lock_version + 1,
                }
            )
            updated_aggregate = aggregate.model_copy(
                update={
                    "current_status": CampaignStatus.APPROVED,
                    "current_progress": 98,
                    "updated_at": now,
                    "lock_version": aggregate.lock_version + 1,
                    "event_sequence": aggregate.event_sequence + 1,
                }
            )
            approved_event = CampaignEvent(
                event_id=uuid4(),
                campaign_id=current.campaign_id,
                campaign_version=current.campaign_version,
                event_sequence=aggregate.event_sequence + 1,
                event_type=CampaignEventType.APPROVED,
                status=CampaignStatus.APPROVED,
                step=None,
                progress_percent=98,
                occurred_at=now,
                actor=Actor.FASTAPI,
                correlation_id=correlation_id,
                job_id=resume_job_id,
                details={},
            )
            await self.repository.approve(updated_aggregate, updated_version, approval, [approved_event])
            current = updated_version

        message = SQSJobMessage(
            schema_version=1,
            job_id=current.job_id,
            campaign_id=current.campaign_id,
            campaign_version=current.campaign_version,
            operation=SQSOperation.RESUME,
            requested_step=None,
            revision_scope=None,
            idempotency_key=str(current.job_id),
            correlation_id=correlation_id,
            requested_at=approval.approved_at,
            attempt=0,
            trace_id=correlation_id.hex,
        )
        result = await self.queue.submit(message)
        if not result.accepted or result.job_id != message.job_id:
            raise QueueSubmissionFailure("queue submission failed")

        return ApprovalResponse(
            campaign_id=current.campaign_id,
            campaign_version=current.campaign_version,
            approval_id=approval.approval_id,
            status=CampaignStatus.APPROVED,
            job_id=current.job_id,
        )

    async def cancel(
        self, campaign_id: UUID, campaign_version: int, request: CancellationRequest, correlation_id: UUID
    ) -> CancellationResponse:
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        aggregate, current = record
        if current.campaign_version != campaign_version:
            raise InvalidStateTransition("cancellation must target the current campaign version")

        if current.status == CampaignStatus.CANCELLED:
            if current.cancellation_reason != request.reason:
                raise DuplicateJobConflict("cancellation already recorded with a different reason")
            return CancellationResponse(
                campaign_id=current.campaign_id,
                campaign_version=current.campaign_version,
                status=CampaignStatus.CANCELLED,
                cancellation_pending=False,
            )

        try:
            validate_transition(current.status, CampaignStatus.CANCELLED)  # type: ignore[no-untyped-call]
        except ValueError as exc:
            raise InvalidStateTransition(str(exc)) from None

        pending = current.status in _CANCELLATION_PENDING_STATUSES
        now = datetime.now(UTC)
        updated_version = current.model_copy(
            update={
                "status": CampaignStatus.CANCELLED,
                "cancellation_reason": request.reason,
                "cancelled_at": now,
                "updated_at": now,
                "lock_version": current.lock_version + 1,
            }
        )
        events = [
            CampaignEvent(
                event_id=uuid4(),
                campaign_id=current.campaign_id,
                campaign_version=current.campaign_version,
                event_sequence=aggregate.event_sequence + 1,
                event_type=CampaignEventType.CANCEL_REQUESTED,
                status=CampaignStatus.CANCELLED,
                step=current.current_step,
                progress_percent=current.progress_percent,
                occurred_at=now,
                actor=Actor.FASTAPI,
                correlation_id=correlation_id,
                job_id=current.job_id,
                details={},
            )
        ]
        if not pending:
            # "No provider call active": FastAPI durably sets CANCELLED itself in the
            # same transaction (docs/contracts/campaign-lifecycle.md's cancellation
            # section). When pending, only CANCEL_REQUESTED is recorded here -- the
            # eventual CANCELLED event for that path is worker-owned (C5B).
            events.append(
                CampaignEvent(
                    event_id=uuid4(),
                    campaign_id=current.campaign_id,
                    campaign_version=current.campaign_version,
                    event_sequence=aggregate.event_sequence + 2,
                    event_type=CampaignEventType.CANCELLED,
                    status=CampaignStatus.CANCELLED,
                    step=current.current_step,
                    progress_percent=current.progress_percent,
                    occurred_at=now,
                    actor=Actor.FASTAPI,
                    correlation_id=correlation_id,
                    job_id=current.job_id,
                    details={},
                )
            )
        updated_aggregate = aggregate.model_copy(
            update={
                "current_status": CampaignStatus.CANCELLED,
                "updated_at": now,
                "lock_version": aggregate.lock_version + 1,
                "event_sequence": aggregate.event_sequence + len(events),
            }
        )
        await self.repository.cancel(updated_aggregate, updated_version, events)

        return CancellationResponse(
            campaign_id=updated_version.campaign_id,
            campaign_version=updated_version.campaign_version,
            status=CampaignStatus.CANCELLED,
            cancellation_pending=pending,
        )

    async def retry(
        self, campaign_id: UUID, campaign_version: int, request: RetryRequest, correlation_id: UUID
    ) -> RetryResponse:
        del request  # RetryRequest carries no fields; retry identity is the durable record itself
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        aggregate, current = record
        if current.campaign_version != campaign_version:
            raise InvalidStateTransition("retry must target the current campaign version")

        now = datetime.now(UTC)

        if current.status == CampaignStatus.FAILED:
            if not current.retry.retryable:
                raise InvalidStateTransition("campaign is not in a retryable state")
            try:
                validate_transition(current.status, CampaignStatus.QUEUED)  # type: ignore[no-untyped-call]
            except ValueError as exc:
                raise InvalidStateTransition(str(exc)) from None

            retry_job_id = uuid5(current.campaign_id, f"RETRY:{current.campaign_version}:{current.retry.attempt}")
            updated_version = current.model_copy(
                update={
                    "status": CampaignStatus.QUEUED,
                    "job_id": retry_job_id,
                    "updated_at": now,
                    "lock_version": current.lock_version + 1,
                }
            )
            updated_aggregate = aggregate.model_copy(
                update={
                    "current_status": CampaignStatus.QUEUED,
                    "updated_at": now,
                    "lock_version": aggregate.lock_version + 1,
                    "event_sequence": aggregate.event_sequence + 1,
                }
            )
            retry_event = CampaignEvent(
                event_id=uuid4(),
                campaign_id=current.campaign_id,
                campaign_version=current.campaign_version,
                event_sequence=aggregate.event_sequence + 1,
                event_type=CampaignEventType.RETRY_SCHEDULED,
                status=CampaignStatus.QUEUED,
                step=None,
                progress_percent=current.progress_percent,
                occurred_at=now,
                actor=Actor.FASTAPI,
                correlation_id=correlation_id,
                job_id=retry_job_id,
                details={},
            )
            await self.repository.retry(updated_aggregate, updated_version, [retry_event])
            current = updated_version
        elif current.status == CampaignStatus.QUEUED:
            expected_job_id = uuid5(current.campaign_id, f"RETRY:{current.campaign_version}:{current.retry.attempt}")
            if current.job_id != expected_job_id:
                raise InvalidStateTransition("campaign is not awaiting this retry attempt")
        else:
            raise InvalidStateTransition("campaign is not in a retryable state")

        resume_step = current.retry.resume_step
        if resume_step is None:
            raise InvalidStateTransition("failed campaign is missing a resume step")

        # A PACKAGE-step failure happened inside the RESUME graph (prepare_final_package),
        # which build_start_graph does not contain -- resubmitting START would silently
        # re-run generation and land back at READY_FOR_REVIEW instead of resuming
        # finalization, so route that one case back through RESUME instead.
        operation = SQSOperation.RESUME if resume_step == WorkflowStep.PACKAGE else SQSOperation.START
        message = SQSJobMessage(
            schema_version=1,
            job_id=current.job_id,
            campaign_id=current.campaign_id,
            campaign_version=current.campaign_version,
            operation=operation,
            requested_step=None,
            revision_scope=None,
            idempotency_key=str(current.job_id),
            correlation_id=correlation_id,
            requested_at=current.updated_at,
            attempt=0,
            trace_id=correlation_id.hex,
        )
        result = await self.queue.submit(message)
        if not result.accepted or result.job_id != message.job_id:
            raise QueueSubmissionFailure("queue submission failed")

        return RetryResponse(
            campaign_id=current.campaign_id,
            campaign_version=current.campaign_version,
            job_id=current.job_id,
            status=CampaignStatus.QUEUED,
            resume_step=resume_step,
            attempt=current.retry.attempt,
        )

    async def revise(
        self, campaign_id: UUID, campaign_version: int, request: RevisionRequest, correlation_id: UUID
    ) -> RevisionResponse:
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        aggregate, current = record

        earliest_affected_step = earliest_regeneration_step(request.scope)
        expected_feedback = RevisionFeedback(
            parent_version=campaign_version,
            reason=request.reason,
            scope=request.scope,
            affected_artifact_ids=request.affected_artifact_ids,
            earliest_affected_step=earliest_affected_step,
        )

        # Only a still-QUEUED child is a safe idempotent replay target: once the worker has moved it
        # past QUEUED (approved/finalized/failed/etc.), current.job_id has been rotated away from the
        # deterministic REGENERATE job id this branch would resubmit, so re-queuing here would target a
        # message identity the worker has never seen against a version that may already be immutable.
        is_replay_target = (
            current.campaign_version == campaign_version + 1
            and current.parent_version == campaign_version
            and current.status == CampaignStatus.QUEUED
        )
        if is_replay_target and current.revision is not None:
            if current.revision != expected_feedback:
                raise DuplicateJobConflict("revision already recorded with different feedback")
            message = self._build_regenerate_message(current, correlation_id)
            result = await self.queue.submit(message)
            if not result.accepted or result.job_id != message.job_id:
                raise QueueSubmissionFailure("queue submission failed")
            return RevisionResponse(
                campaign_id=current.campaign_id,
                parent_version=campaign_version,
                campaign_version=current.campaign_version,
                job_id=current.job_id,
                status=CampaignStatus.QUEUED,
                earliest_affected_step=earliest_affected_step,
            )

        if current.campaign_version != campaign_version:
            raise InvalidStateTransition("revision must target the current campaign version")
        if current.status != CampaignStatus.READY_FOR_REVIEW:
            raise InvalidStateTransition("campaign is not in a revisable state")

        now = datetime.now(UTC)
        new_version_number = campaign_version + 1
        new_job_id = uuid5(current.campaign_id, f"REGENERATE:{new_version_number}")

        target_index = _STEP_ORDER.index(earliest_affected_step)
        reused_steps = _STEP_ORDER[:target_index]
        reuse_strategy = WorkflowStep.STRATEGY in reused_steps
        reuse_copy = WorkflowStep.COPY in reused_steps
        reuse_storyboard = WorkflowStep.STORYBOARD in reused_steps
        reuse_images = WorkflowStep.IMAGES in reused_steps
        reuse_voice = WorkflowStep.VOICEOVER in reused_steps

        child_version = CampaignVersion(
            schema_version=1,
            campaign_id=current.campaign_id,
            campaign_version=new_version_number,
            parent_version=campaign_version,
            job_id=new_job_id,
            status=CampaignStatus.QUEUED,
            current_step=earliest_affected_step,
            progress_percent=_REVISION_PROGRESS_FLOOR[earliest_affected_step],
            brief=current.brief,
            constraints=current.constraints,
            strategy=current.strategy if reuse_strategy else None,
            copy=current.campaign_copy if reuse_copy else None,
            storyboard=current.storyboard if reuse_storyboard else None,
            image_prompts=list(current.image_prompts) if reuse_images else [],
            image_artifacts=list(current.image_artifacts) if reuse_images else [],
            voice_artifact=current.voice_artifact if reuse_voice else None,
            video_artifact=None,
            review_package=None,
            package_artifact=None,
            revision=expected_feedback,
            approval=None,
            completed_steps=list(reused_steps),
            retry=RetryMetadata(),
            error=None,
            created_at=now,
            updated_at=now,
        )
        frozen_parent = current.model_copy(
            update={
                "status": CampaignStatus.REVISION_REQUESTED,
                "updated_at": now,
                "lock_version": current.lock_version + 1,
            }
        )
        updated_aggregate = aggregate.model_copy(
            update={
                "current_version": new_version_number,
                "current_status": CampaignStatus.QUEUED,
                "current_progress": child_version.progress_percent,
                "updated_at": now,
                "lock_version": aggregate.lock_version + 1,
                "event_sequence": aggregate.event_sequence + 1,
            }
        )
        revision_event = CampaignEvent(
            event_id=uuid4(),
            campaign_id=current.campaign_id,
            campaign_version=campaign_version,  # describes the frozen parent, not the new child
            event_sequence=aggregate.event_sequence + 1,
            event_type=CampaignEventType.REVISION_REQUESTED,
            status=CampaignStatus.REVISION_REQUESTED,
            step=None,
            progress_percent=current.progress_percent,
            occurred_at=now,
            actor=Actor.FASTAPI,
            correlation_id=correlation_id,
            job_id=current.job_id,
            details={},
        )
        await self.repository.revise(updated_aggregate, frozen_parent, child_version, [revision_event])

        message = self._build_regenerate_message(child_version, correlation_id)
        result = await self.queue.submit(message)
        if not result.accepted or result.job_id != message.job_id:
            raise QueueSubmissionFailure("queue submission failed")

        return RevisionResponse(
            campaign_id=child_version.campaign_id,
            parent_version=campaign_version,
            campaign_version=new_version_number,
            job_id=new_job_id,
            status=CampaignStatus.QUEUED,
            earliest_affected_step=earliest_affected_step,
        )

    @staticmethod
    def _build_regenerate_message(version: CampaignVersion, correlation_id: UUID) -> SQSJobMessage:
        revision = version.revision
        if revision is None:
            raise InvalidStateTransition("revision metadata is missing for regeneration")
        return SQSJobMessage(
            schema_version=1,
            job_id=version.job_id,
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            operation=SQSOperation.REGENERATE,
            requested_step=revision.earliest_affected_step,
            revision_scope=revision.scope,
            idempotency_key=str(version.job_id),
            correlation_id=correlation_id,
            requested_at=version.updated_at,
            attempt=0,
            trace_id=correlation_id.hex,
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
            artifacts=[
                *v.image_artifacts,
                *([v.voice_artifact] if v.voice_artifact is not None else []),
                *([v.video_artifact] if v.video_artifact is not None else []),
                *([v.package_artifact] if v.package_artifact is not None else []),
            ],
            revision=v.revision,
            approval=v.approval,
            completed_steps=v.completed_steps,
            retry_eligible=v.status == CampaignStatus.FAILED and v.retry.retryable,
            error=v.error,
            cancellation_reason=v.cancellation_reason,
            cancelled_at=v.cancelled_at,
            created_at=v.created_at,
            updated_at=v.updated_at,
            title=a.title,
            current_version=a.current_version,
            latest_final_version=a.latest_final_version,
            event_sequence=a.event_sequence,
            review_manifest_checksum=v.review_package.manifest_checksum if v.review_package is not None else None,
            actions={},
        )

    async def get(self, campaign_id: UUID) -> CampaignDetailResponse:
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        return self.detail(*record)

    async def artifacts(
        self,
        campaign_id: UUID,
        campaign_version: int | None,
        artifact_type: ArtifactType | None,
    ) -> CampaignArtifactsResponse:
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        aggregate, current = record
        if campaign_version is None or campaign_version == aggregate.current_version:
            version = current
        else:
            historical_version = await self.repository.get_version(campaign_id, campaign_version)
            if historical_version is None:
                raise CampaignNotFound("campaign version not found")
            version = historical_version

        stored = [
            *version.image_artifacts,
            *([version.voice_artifact] if version.voice_artifact is not None else []),
            *([version.video_artifact] if version.video_artifact is not None else []),
            *([version.package_artifact] if version.package_artifact is not None else []),
        ]
        if artifact_type is not None:
            stored = [artifact for artifact in stored if artifact.artifact_type == artifact_type]

        items: list[PublicArtifactReference] = []
        for artifact in stored:
            values = artifact.model_dump(mode="python")
            if artifact.artifact_type == ArtifactType.IMAGE and artifact.scene_number is not None:
                if self.artifact_signer is None:
                    raise RepositoryFailure("artifact download service unavailable")
                try:
                    download_url, expires_at = await self.artifact_signer.sign_image(
                        campaign_id,
                        version.campaign_version,
                        artifact.scene_number,
                    )
                except Exception:
                    raise RepositoryFailure("artifact access temporarily unavailable") from None
                values.update(download_url=download_url, download_url_expires_at=expires_at)
            elif artifact.artifact_type == ArtifactType.AUDIO:
                if self.artifact_signer is None:
                    raise RepositoryFailure("artifact download service unavailable")
                try:
                    download_url, expires_at = await self.artifact_signer.sign_audio(
                        campaign_id,
                        version.campaign_version,
                    )
                except Exception:
                    raise RepositoryFailure("artifact access temporarily unavailable") from None
                values.update(download_url=download_url, download_url_expires_at=expires_at)
            elif artifact.artifact_type == ArtifactType.VIDEO:
                if self.artifact_signer is None:
                    raise RepositoryFailure("artifact download service unavailable")
                try:
                    download_url, expires_at = await self.artifact_signer.sign_video(
                        campaign_id,
                        version.campaign_version,
                    )
                except Exception:
                    raise RepositoryFailure("artifact access temporarily unavailable") from None
                values.update(download_url=download_url, download_url_expires_at=expires_at)
            elif artifact.artifact_type == ArtifactType.FINAL_PACKAGE:
                if self.artifact_signer is None:
                    raise RepositoryFailure("artifact download service unavailable")
                try:
                    download_url, expires_at = await self.artifact_signer.sign_package(
                        campaign_id,
                        version.campaign_version,
                    )
                except Exception:
                    raise RepositoryFailure("artifact access temporarily unavailable") from None
                values.update(download_url=download_url, download_url_expires_at=expires_at)
            items.append(PublicArtifactReference.model_validate(values))
        return CampaignArtifactsResponse(
            campaign_id=campaign_id,
            campaign_version=version.campaign_version,
            items=items,
        )

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

    async def events(self, campaign_id: UUID, cursor: str | None, limit: int) -> CampaignEventsResponse:
        record = await self.repository.get(campaign_id)
        if record is None:
            raise CampaignNotFound("campaign not found")
        aggregate, current = record
        after_sequence = 0
        if cursor is not None:
            try:
                after_sequence = int(cursor)
            except ValueError:
                raise InvalidCursor("cursor is not a valid event sequence") from None
            if after_sequence < 0:
                raise InvalidCursor("cursor is not a valid event sequence")
        fetched = await self.repository.list_events(campaign_id, after_sequence, limit + 1)
        has_more = len(fetched) > limit
        items = fetched[:limit]
        next_cursor = str(items[-1].event_sequence) if has_more else None
        return CampaignEventsResponse(
            campaign_id=campaign_id,
            campaign_version=current.campaign_version,
            items=items,
            next_cursor=next_cursor,
            latest_sequence=aggregate.event_sequence,
            terminal=is_terminal(current.status),  # type: ignore[no-untyped-call]
        )
