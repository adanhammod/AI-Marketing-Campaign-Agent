from typing import Any

import boto3  # type: ignore[import-untyped]

from campaign_api.config import Settings
from campaign_api.repositories.campaign_repository import CampaignRepository
from campaign_api.repositories.dynamodb_campaign_repository import DynamoDBCampaignRepository
from campaign_api.repositories.in_memory_campaign_repository import InMemoryCampaignRepository


def create_dynamodb_repository(client: Any, settings: Settings) -> DynamoDBCampaignRepository:
    return DynamoDBCampaignRepository(client, settings.dynamodb_table_name)


def create_repository(settings: Settings, client: Any | None = None) -> CampaignRepository:
    settings.validate()
    if settings.repository_backend == "memory":
        return InMemoryCampaignRepository()
    resolved_client = client or boto3.client("dynamodb", region_name=settings.aws_region)
    return create_dynamodb_repository(resolved_client, settings)
