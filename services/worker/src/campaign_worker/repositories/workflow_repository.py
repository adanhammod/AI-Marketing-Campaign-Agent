from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep
from campaign_contracts.sqs import SQSJobMessage
from campaign_contracts.steps import WorkflowStepRecord


@dataclass(slots=True)
class LeaseContext:
    owner: str
    lock_version: int
    expires_at: datetime


class WorkflowRepository(ABC):
    @abstractmethod
    async def load_version(self, message: SQSJobMessage) -> CampaignVersion | None: ...

    @abstractmethod
    async def acquire_lease(
        self, message: SQSJobMessage, owner: str, now: datetime, expires_at: datetime
    ) -> LeaseContext: ...

    @abstractmethod
    async def heartbeat(
        self, message: SQSJobMessage, lease: LeaseContext, now: datetime, expires_at: datetime
    ) -> LeaseContext: ...

    @abstractmethod
    async def is_completed(self, message: SQSJobMessage) -> bool: ...

    @abstractmethod
    async def complete(self, message: SQSJobMessage, lease: LeaseContext, completed_at: datetime) -> None: ...

    @abstractmethod
    async def release(self, message: SQSJobMessage, lease: LeaseContext) -> None: ...

    @abstractmethod
    async def record_exhausted(self, message: SQSJobMessage, receive_count: int, now: datetime) -> None: ...

    @abstractmethod
    async def record_invalid(self, campaign_id: UUID, code: str, message_id: str, now: datetime) -> None: ...

    @abstractmethod
    async def available(self) -> bool: ...

    @abstractmethod
    async def get_step(
        self, campaign_id: UUID, campaign_version: int, step: WorkflowStep
    ) -> WorkflowStepRecord | None: ...

    @abstractmethod
    async def save_step(self, record: WorkflowStepRecord) -> None: ...

    @abstractmethod
    async def save_version(self, version: CampaignVersion, lease: LeaseContext) -> None: ...
