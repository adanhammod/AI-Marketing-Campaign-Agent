import asyncio
from uuid import UUID

from campaign_contracts.campaign import ApprovalRecord, CampaignAggregateMetadata, CampaignVersion

from campaign_api.exceptions import DuplicateCampaign, InvalidStateTransition, RepositoryFailure

from .campaign_repository import CampaignRepository


class InMemoryCampaignRepository(CampaignRepository):
    def __init__(self) -> None:
        self._records: dict[UUID, tuple[CampaignAggregateMetadata, CampaignVersion]] = {}
        self._lock = asyncio.Lock()
        self.fail_writes = False

    async def create_initial(self, aggregate: CampaignAggregateMetadata, version: CampaignVersion) -> None:
        async with self._lock:
            if self.fail_writes:
                raise RepositoryFailure("repository write failed")
            if aggregate.campaign_id in self._records:
                raise DuplicateCampaign("campaign already exists")
            if version.campaign_version != 1 or version.campaign_id != aggregate.campaign_id:
                raise RepositoryFailure("invalid initial version")
            self._records[aggregate.campaign_id] = (aggregate.model_copy(deep=True), version.model_copy(deep=True))

    async def get(self, campaign_id: UUID) -> tuple[CampaignAggregateMetadata, CampaignVersion] | None:
        record = self._records.get(campaign_id)
        return None if record is None else (record[0].model_copy(deep=True), record[1].model_copy(deep=True))

    async def list(self, *, offset: int, limit: int) -> list[tuple[CampaignAggregateMetadata, CampaignVersion]]:
        records = sorted(self._records.values(), key=lambda x: (x[0].created_at, str(x[0].campaign_id)), reverse=True)[
            offset : offset + limit
        ]
        return [(a.model_copy(deep=True), v.model_copy(deep=True)) for a, v in records]

    async def exists(self, campaign_id: UUID) -> bool:
        return campaign_id in self._records

    async def replace_current(self, aggregate: CampaignAggregateMetadata, version: CampaignVersion) -> None:
        async with self._lock:
            current = self._records.get(aggregate.campaign_id)
            if current is None:
                raise RepositoryFailure("campaign missing during update")
            if current[1].campaign_version != version.campaign_version:
                raise RepositoryFailure("immutable version mismatch")
            self._records[aggregate.campaign_id] = (aggregate.model_copy(deep=True), version.model_copy(deep=True))

    async def approve(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, approval: ApprovalRecord
    ) -> None:
        async with self._lock:
            current = self._records.get(aggregate.campaign_id)
            if current is None:
                raise RepositoryFailure("campaign missing during update")
            if current[1].campaign_version != version.campaign_version:
                raise RepositoryFailure("immutable version mismatch")
            if current[1].approval is not None:
                raise InvalidStateTransition("approval already recorded")
            self._records[aggregate.campaign_id] = (aggregate.model_copy(deep=True), version.model_copy(deep=True))

    async def cancel(self, aggregate: CampaignAggregateMetadata, version: CampaignVersion) -> None:
        async with self._lock:
            current = self._records.get(aggregate.campaign_id)
            if current is None:
                raise RepositoryFailure("campaign missing during update")
            if current[1].campaign_version != version.campaign_version:
                raise RepositoryFailure("immutable version mismatch")
            self._records[aggregate.campaign_id] = (aggregate.model_copy(deep=True), version.model_copy(deep=True))

    async def rollback_initial(self, campaign_id: UUID) -> None:
        async with self._lock:
            self._records.pop(campaign_id, None)

    async def available(self) -> bool:
        return True

    async def clear(self) -> None:
        async with self._lock:
            self._records.clear()
