from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from campaign_contracts.sqs import SQSJobMessage


@dataclass(frozen=True, slots=True)
class QueueSubmissionResult:
    job_id: UUID
    accepted: bool


class JobQueue(ABC):
    @abstractmethod
    async def submit(self, message: SQSJobMessage) -> QueueSubmissionResult: ...

    @abstractmethod
    async def available(self) -> bool: ...
