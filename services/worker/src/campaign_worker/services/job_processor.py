from abc import ABC, abstractmethod
from dataclasses import dataclass

from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.sqs import SQSJobMessage

from campaign_worker.repositories.workflow_repository import LeaseContext


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    completed: bool
    marker: str


class JobProcessor(ABC):
    @abstractmethod
    async def process(
        self, message: SQSJobMessage, version: CampaignVersion, lease: LeaseContext
    ) -> ProcessingResult: ...


class NoOpJobProcessor(JobProcessor):
    """Task 9 boundary only: records acceptance without executing workflow nodes."""

    async def process(self, message: SQSJobMessage, version: CampaignVersion, lease: LeaseContext) -> ProcessingResult:
        return ProcessingResult(completed=True, marker="NO_OP_CHECKPOINT_COMMITTED")
