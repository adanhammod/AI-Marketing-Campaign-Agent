import asyncio
import builtins
from uuid import UUID

from campaign_contracts.campaign import ApprovalRecord, CampaignAggregateMetadata, CampaignVersion
from campaign_contracts.events import CampaignEvent

from campaign_api.exceptions import DuplicateCampaign, InvalidStateTransition, RepositoryFailure

from .campaign_repository import CampaignRepository


class InMemoryCampaignRepository(CampaignRepository):
    def __init__(self) -> None:
        self._records: dict[UUID, tuple[CampaignAggregateMetadata, CampaignVersion]] = {}
        self._versions: dict[tuple[UUID, int], CampaignVersion] = {}
        self._events: dict[UUID, builtins.list[CampaignEvent]] = {}
        self._lock = asyncio.Lock()
        self.fail_writes = False

    def _append_events(self, campaign_id: UUID, events: builtins.list[CampaignEvent]) -> None:
        if not events:
            return
        self._events.setdefault(campaign_id, []).extend(e.model_copy(deep=True) for e in events)

    async def create_initial(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, events: builtins.list[CampaignEvent]
    ) -> None:
        async with self._lock:
            if self.fail_writes:
                raise RepositoryFailure("repository write failed")
            if aggregate.campaign_id in self._records:
                raise DuplicateCampaign("campaign already exists")
            if version.campaign_version != 1 or version.campaign_id != aggregate.campaign_id:
                raise RepositoryFailure("invalid initial version")
            self._records[aggregate.campaign_id] = (aggregate.model_copy(deep=True), version.model_copy(deep=True))
            self._versions[(version.campaign_id, version.campaign_version)] = version.model_copy(deep=True)
            self._append_events(aggregate.campaign_id, events)

    async def get(self, campaign_id: UUID) -> tuple[CampaignAggregateMetadata, CampaignVersion] | None:
        record = self._records.get(campaign_id)
        return None if record is None else (record[0].model_copy(deep=True), record[1].model_copy(deep=True))

    async def get_version(self, campaign_id: UUID, campaign_version: int) -> CampaignVersion | None:
        version = self._versions.get((campaign_id, campaign_version))
        return None if version is None else version.model_copy(deep=True)

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

            self._versions[(version.campaign_id, version.campaign_version)] = version.model_copy(deep=True)

    async def approve(
        self,
        aggregate: CampaignAggregateMetadata,
        version: CampaignVersion,
        approval: ApprovalRecord,
        events: builtins.list[CampaignEvent],
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
            self._append_events(aggregate.campaign_id, events)
            self._versions[(version.campaign_id, version.campaign_version)] = version.model_copy(deep=True)

    async def cancel(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, events: builtins.list[CampaignEvent]
    ) -> None:
        async with self._lock:
            current = self._records.get(aggregate.campaign_id)
            if current is None:
                raise RepositoryFailure("campaign missing during update")
            if current[1].campaign_version != version.campaign_version:
                raise RepositoryFailure("immutable version mismatch")
            self._records[aggregate.campaign_id] = (aggregate.model_copy(deep=True), version.model_copy(deep=True))
            self._append_events(aggregate.campaign_id, events)
            self._versions[(version.campaign_id, version.campaign_version)] = version.model_copy(deep=True)

    async def retry(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, events: builtins.list[CampaignEvent]
    ) -> None:
        async with self._lock:
            current = self._records.get(aggregate.campaign_id)
            if current is None:
                raise RepositoryFailure("campaign missing during update")
            if current[1].campaign_version != version.campaign_version:
                raise RepositoryFailure("immutable version mismatch")
            self._records[aggregate.campaign_id] = (aggregate.model_copy(deep=True), version.model_copy(deep=True))
            self._append_events(aggregate.campaign_id, events)
            self._versions[(version.campaign_id, version.campaign_version)] = version.model_copy(deep=True)

    async def revise(
        self,
        aggregate: CampaignAggregateMetadata,
        parent_version: CampaignVersion,
        child_version: CampaignVersion,
        events: builtins.list[CampaignEvent],
    ) -> None:
        async with self._lock:
            current = self._records.get(aggregate.campaign_id)
            if current is None:
                raise RepositoryFailure("campaign missing during update")
            if current[1].campaign_version != parent_version.campaign_version:
                raise RepositoryFailure("immutable version mismatch")
            self._records[aggregate.campaign_id] = (
                aggregate.model_copy(deep=True),
                child_version.model_copy(deep=True),
            )
            self._append_events(aggregate.campaign_id, events)
            self._versions[(parent_version.campaign_id, parent_version.campaign_version)] = parent_version.model_copy(
                deep=True
            )
            self._versions[(child_version.campaign_id, child_version.campaign_version)] = child_version.model_copy(
                deep=True
            )

    async def rollback_initial(self, campaign_id: UUID, events: builtins.list[CampaignEvent]) -> None:
        del events  # the bulk pop below already removes every event written for this campaign
        async with self._lock:
            self._records.pop(campaign_id, None)
            self._events.pop(campaign_id, None)

            for key in [key for key in self._versions if key[0] == campaign_id]:
                self._versions.pop(key, None)

    async def list_events(self, campaign_id: UUID, after_sequence: int, limit: int) -> builtins.list[CampaignEvent]:
        events = self._events.get(campaign_id, [])
        deduped: dict[UUID, CampaignEvent] = {}
        for event in events:
            if event.event_sequence > after_sequence:
                deduped[event.event_id] = event
        ordered = sorted(deduped.values(), key=lambda e: e.event_sequence)
        return [e.model_copy(deep=True) for e in ordered[:limit]]

    async def available(self) -> bool:
        return True

    async def clear(self) -> None:
        async with self._lock:
            self._records.clear()
            self._events.clear()
            self._versions.clear()
