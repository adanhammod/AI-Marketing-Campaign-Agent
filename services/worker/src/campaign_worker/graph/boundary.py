from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from campaign_contracts.enums import StepStatus, WorkflowStep
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.repositories.workflow_repository import WorkflowRepository

from .state import GraphState

NodeFn = Callable[[GraphState], Awaitable[GraphState]]


def with_step_tracking(step: WorkflowStep, repository: WorkflowRepository) -> Callable[[NodeFn], NodeFn]:
    def decorator(fn: NodeFn) -> NodeFn:
        async def wrapped(state: GraphState) -> GraphState:
            version = state["version"]
            existing = await repository.get_step(version.campaign_id, version.campaign_version, step)
            if existing is not None and existing.status in (StepStatus.SUCCEEDED, StepStatus.REUSED):
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
                )
            )
            result = await fn(state)
            completed_at = datetime.now(UTC)
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
                )
            )
            return result

        return wrapped

    return decorator
