from typing import Any
from uuid import UUID

from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignVersion
from campaign_contracts.enums import CampaignStatus


class DuplicateCampaign(Exception):
    pass


class CampaignNotFound(Exception):
    pass


class MarketingMCPService:
    """Scaffold: in-memory store standing in for the real DynamoDB/S3-backed implementation."""

    def __init__(self) -> None:
        self._campaigns: dict[UUID, tuple[CampaignAggregateMetadata, CampaignVersion]] = {}
        self._idempotency: dict[tuple[UUID, str], str] = {}
        self._packages: dict[tuple[UUID, int], str] = {}
        self._assets: list[Any] = []

    async def create_campaign(
        self, aggregate: CampaignAggregateMetadata, version: CampaignVersion, *, idempotency_key: str
    ) -> tuple[CampaignAggregateMetadata, CampaignVersion]:
        existing_key = self._idempotency.get((aggregate.campaign_id, "create_campaign"))
        if existing_key is not None:
            if existing_key != idempotency_key:
                raise DuplicateCampaign("campaign already exists with a different idempotency key")
            return self._campaigns[aggregate.campaign_id]
        self._idempotency[(aggregate.campaign_id, "create_campaign")] = idempotency_key
        self._campaigns[aggregate.campaign_id] = (aggregate, version)
        return aggregate, version

    async def get_campaign(self, campaign_id: UUID) -> tuple[CampaignAggregateMetadata, CampaignVersion] | None:
        return self._campaigns.get(campaign_id)

    async def update_campaign(
        self, campaign_id: UUID, patch: dict[str, Any], *, idempotency_key: str
    ) -> CampaignVersion:
        record = self._campaigns.get(campaign_id)
        if record is None:
            raise CampaignNotFound(str(campaign_id))
        aggregate, version = record
        updated = version.model_copy(update=patch)
        self._campaigns[campaign_id] = (aggregate, updated)
        return updated

    async def update_campaign_status(
        self, campaign_id: UUID, campaign_version: int, status: CampaignStatus, *, idempotency_key: str
    ) -> CampaignVersion:
        record = self._campaigns.get(campaign_id)
        if record is None:
            raise CampaignNotFound(str(campaign_id))
        aggregate, version = record
        updated = version.model_copy(update={"status": status})
        self._campaigns[campaign_id] = (aggregate, updated)
        return updated

    async def save_campaign_content(
        self, campaign_id: UUID, campaign_version: int, field: str, payload: Any, *, idempotency_key: str
    ) -> CampaignVersion:
        record = self._campaigns.get(campaign_id)
        if record is None:
            raise CampaignNotFound(str(campaign_id))
        aggregate, version = record
        updated = version.model_copy(update={field: payload})
        self._campaigns[campaign_id] = (aggregate, updated)
        return updated

    async def save_asset_metadata(self, artifact: Any) -> None:
        self._assets.append(artifact)

    async def validate_campaign_package(self, campaign_id: UUID) -> bool:
        record = self._campaigns.get(campaign_id)
        if record is None:
            return False
        _, version = record
        return version.strategy is not None and version.campaign_copy is not None and version.storyboard is not None

    async def prepare_delivery_package(self, campaign_id: UUID, campaign_version: int, *, idempotency_key: str) -> str:
        key = (campaign_id, campaign_version)
        if key in self._packages:
            return self._packages[key]
        package_id = f"package-{campaign_id}-{campaign_version}"
        self._packages[key] = package_id
        return package_id
