from typing import Any

from campaign_api.config import Settings
from campaign_api.repositories.dynamodb_campaign_repository import DynamoDBCampaignRepository


def create_dynamodb_repository(client: Any, settings: Settings) -> DynamoDBCampaignRepository:
    return DynamoDBCampaignRepository(client, settings.dynamodb_table_name)
