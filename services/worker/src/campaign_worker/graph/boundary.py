from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from campaign_contracts.enums import Actor, CampaignEventType, StepStatus, WorkflowStep
from campaign_contracts.events import CampaignEvent
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.events import deterministic_event_id
from campaign_worker.repositories.workflow_repository import WorkflowRepository

from .state import GraphState

NodeFn = Callable[[GraphState], Awaitable[GraphState]]


def _step_event(
    state: GraphState,
    step: WorkflowStep,
    event_type: CampaignEventType,
    *,
    occurred_at: datetime,
    details: dict[str, Any] | None = None,
) -> CampaignEvent:
    version = state["version"]
    # version.retry.attempt only advances on a real handle_failure-driven retry cycle, so
    # it discriminates genuine re-execution attempts from a plain SQS redelivery of the
    # same attempt, which must derive the exact same event_id (see events.py).
    discriminator = f"{step.value}:{version.retry.attempt}"
    return CampaignEvent(
        event_id=deterministic_event_id(version.campaign_id, version.campaign_version, event_type, discriminator),
        campaign_id=version.campaign_id,
        campaign_version=version.campaign_version,
        event_sequence=1,  # placeholder -- the repository assigns the real value transactionally
        event_type=event_type,
        status=version.status,
        step=step,
        progress_percent=version.progress_percent,
        occurred_at=occurred_at,
        actor=Actor.LANGGRAPH_WORKER,
        correlation_id=state.get("correlation_id") or uuid4(),
        job_id=version.job_id,
        details=details or {},
    )


def with_step_tracking(step: WorkflowStep, repository: WorkflowRepository) -> Callable[[NodeFn], NodeFn]:
    def decorator(fn: NodeFn) -> NodeFn:
        async def wrapped(state: GraphState) -> GraphState:
            version = state["version"]
            existing = await repository.get_step(version.campaign_id, version.campaign_version, step)
            if existing is not None and existing.status in (StepStatus.SUCCEEDED, StepStatus.REUSED, StepStatus.SKIPPED):
                return state
            now = datetime.now(UTC)
            await repository.save_step(
                WorkflowStepRecord(
                    campaign_id=version.campaign_id,
                    campaign_version=version.campaign_version,
                    step=step,
                    status=StepStatus.RUNNING,
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                [_step_event(state, step, CampaignEventType.STEP_STARTED, occurred_at=now)],
            )
            result = await fn(state)
            skipped = bool(result.pop("_step_skipped", False))
            skip_reason = result.pop("_skip_reason", None)
            completed_at = datetime.now(UTC)
            if skipped:
                await repository.save_step(
                    WorkflowStepRecord(
                        campaign_id=version.campaign_id,
                        campaign_version=version.campaign_version,
                        step=step,
                        status=StepStatus.SKIPPED,
                        started_at=now,
                        completed_at=completed_at,
                        created_at=now,
                        updated_at=completed_at,
                    ),
                    [
                        _step_event(
                            result,
                            step,
                            CampaignEventType.STEP_SKIPPED,
                            occurred_at=completed_at,
                            details={"reason": skip_reason} if skip_reason else None,
                        )
                    ],
                )
            else:
                await repository.save_step(
                    WorkflowStepRecord(
                        campaign_id=version.campaign_id,
                        campaign_version=version.campaign_version,
                        step=step,
                        status=StepStatus.SUCCEEDED,
                        started_at=now,
                        completed_at=completed_at,
                        created_at=now,
                        updated_at=completed_at,
                    ),
                    [_step_event(result, step, CampaignEventType.STEP_COMPLETED, occurred_at=completed_at)],
                )
            return result

        return wrapped

    return decorator


class NodeCancelled(Exception):
    def __init__(self, step: str) -> None:
        super().__init__(f"node cancelled before running: {step}")
        self.step = step


def with_cancellation_check(is_cancelled: Callable[[], Awaitable[bool]], step: str) -> Callable[[NodeFn], NodeFn]:
    def decorator(fn: NodeFn) -> NodeFn:
        async def wrapped(state: GraphState) -> GraphState:
            if await is_cancelled():
                raise NodeCancelled(step)
            return await fn(state)

        return wrapped

    return decorator


class NodeFailure(Exception):
    """Wraps any exception escaping a STEP-tracked node, attaching which WorkflowStep was
    executing. GraphJobProcessor unwraps this to pass step= through to handle_failure, so
    retry.resume_step and error.workflow_step get populated correctly instead of always None.
    """

    def __init__(self, step: WorkflowStep, error: BaseException) -> None:
        super().__init__(str(error))
        self.step = step
        self.error = error


def with_failure_attribution(step: WorkflowStep) -> Callable[[NodeFn], NodeFn]:
    def decorator(fn: NodeFn) -> NodeFn:
        async def wrapped(state: GraphState) -> GraphState:
            try:
                return await fn(state)
            except BaseException as exc:
                raise NodeFailure(step, exc) from exc

        return wrapped

    return decorator
