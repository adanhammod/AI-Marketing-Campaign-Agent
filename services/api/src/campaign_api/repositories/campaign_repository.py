import builtins
from abc import ABC, abstractmethod
from uuid import UUID

from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignVersion
from campaign_contracts.events import CampaignEvent


class CampaignRepository(ABC):
    @abstractmethod
    async def create_initial(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, events: builtins.list[CampaignEvent]
    ) -> None: ...
    @abstractmethod
    async def get(self, campaign_id: UUID) -> tuple[CampaignAggregateMetadata, CampaignVersion] | None: ...
    async def get_version(self, campaign_id: UUID, campaign_version: int) -> CampaignVersion | None:
        record = await self.get(campaign_id)
        if record is None or record[1].campaign_version != campaign_version:
            return None
        return record[1]

    @abstractmethod
    async def list(self, *, offset: int, limit: int) -> list[tuple[CampaignAggregateMetadata, CampaignVersion]]: ...
    @abstractmethod
    async def exists(self, campaign_id: UUID) -> bool: ...
    @abstractmethod
    async def replace_current(self, aggregate: CampaignAggregateMetadata, version: CampaignVersion) -> None: ...
    @abstractmethod
    async def cancel(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, events: builtins.list[CampaignEvent]
    ) -> None: ...
    @abstractmethod
    async def retry(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, events: builtins.list[CampaignEvent]
    ) -> None: ...
    @abstractmethod
    async def revise(
        self,
        aggregate: CampaignAggregateMetadata,
        parent_version: CampaignVersion | None,
        child_version: CampaignVersion,
        events: builtins.list[CampaignEvent],
    ) -> None:
        """Persist a revision. parent_version=None means the parent version's own
        item must not be written at all (e.g. a FINAL parent stays immutable) --
        only aggregate meta, the new child version, and events are written."""
        ...
    @abstractmethod
    async def rollback_initial(self, campaign_id: UUID, events: builtins.list[CampaignEvent]) -> None: ...
    @abstractmethod
    async def list_events(self, campaign_id: UUID, after_sequence: int, limit: int) -> builtins.list[CampaignEvent]: ...
    @abstractmethod
    async def available(self) -> bool: ...
    @abstractmethod
    async def clear(self) -> None: ...
