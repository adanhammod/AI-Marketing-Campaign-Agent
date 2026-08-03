from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from campaign_api.exceptions import RepositoryFailure
from campaign_api.repositories.dynamodb_campaign_repository import (
    DynamoDBCampaignRepository,
    _marshal_item,
    _unmarshal_item,
)


def test_marshalling_round_trip_and_table_validation():
    native = {"count": 1, "flag": True, "nested": {"value": "x"}}
    assert _unmarshal_item(_marshal_item(native)) == native
    with pytest.raises(ValueError):
        DynamoDBCampaignRepository(MagicMock(), "")


@pytest.mark.asyncio
async def test_safe_client_error_mapping():
    client = MagicMock()
    client.get_item.side_effect = ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "contains private details"}}, "GetItem"
    )
    repo = DynamoDBCampaignRepository(client, "table")
    with pytest.raises(RepositoryFailure, match="campaign read unavailable") as error:
        await repo.exists(__import__("uuid").uuid4())
    assert "private details" not in str(error.value)
