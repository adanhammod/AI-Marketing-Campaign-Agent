import asyncio
import builtins
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignVersion
from campaign_contracts.dynamodb import meta_sk, pk, serialize_meta, serialize_version, version_sk

from campaign_api.exceptions import DuplicateCampaign, InvalidStateTransition, RepositoryFailure
from campaign_api.repositories.campaign_repository import CampaignRepository

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def _marshal_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _SERIALIZER.serialize(value) for key, value in item.items()}


def _unmarshal_item(item: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], {key: _DESERIALIZER.deserialize(value) for key, value in item.items()})


def _model_payload(
    item: dict[str, Any], model: type[CampaignAggregateMetadata] | type[CampaignVersion]
) -> dict[str, Any]:
    accepted = set(model.model_fields)
    accepted.update(field.alias for field in model.model_fields.values() if field.alias)
    return {key: value for key, value in item.items() if key in accepted}


def _meta_item(aggregate: CampaignAggregateMetadata) -> dict[str, Any]:
    item = cast(dict[str, Any], serialize_meta(aggregate))
    item["GSI1PK"] = "CAMPAIGNS"
    item["GSI1SK"] = f"{aggregate.created_at.isoformat()}#{aggregate.campaign_id}"
    if aggregate.current_status is not None:
        item["GSI2PK"] = f"STATUS#{aggregate.current_status.value}"
        item["GSI2SK"] = f"{aggregate.updated_at.isoformat()}#{aggregate.campaign_id}"
    return item


class DynamoDBCampaignRepository(CampaignRepository):
    """Async facade over an injected low-level DynamoDB client."""

    def __init__(self, client: Any, table_name: str) -> None:
        if not table_name:
            raise ValueError("table_name is required")
        self._client = client
        self._table_name = table_name

    async def create_initial(self, aggregate: CampaignAggregateMetadata, version: CampaignVersion) -> None:
        if version.campaign_version != 1 or version.campaign_id != aggregate.campaign_id:
            raise RepositoryFailure("invalid initial campaign version")
        transaction = [
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": _marshal_item(_meta_item(aggregate)),
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": _marshal_item(serialize_version(version)),
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
        ]
        try:
            await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
        except ClientError as exc:
            if self._is_conditional(exc):
                raise DuplicateCampaign("campaign already exists") from None
            raise RepositoryFailure("campaign persistence unavailable") from None

    async def get(self, campaign_id: UUID) -> tuple[CampaignAggregateMetadata, CampaignVersion] | None:
        meta_response = await self._get_item(campaign_id, meta_sk())
        if not meta_response:
            return None
        aggregate = CampaignAggregateMetadata.model_validate(_model_payload(meta_response, CampaignAggregateMetadata))
        version_response = await self._get_item(campaign_id, version_sk(aggregate.current_version))
        if not version_response:
            raise RepositoryFailure("campaign version is missing")
        version = CampaignVersion.model_validate(_model_payload(version_response, CampaignVersion))
        return aggregate, version

    async def list(self, *, offset: int, limit: int) -> list[tuple[CampaignAggregateMetadata, CampaignVersion]]:
        try:
            response = await asyncio.to_thread(
                self._client.query,
                TableName=self._table_name,
                IndexName="GSI1",
                KeyConditionExpression="GSI1PK = :pk",
                ExpressionAttributeValues={":pk": _SERIALIZER.serialize("CAMPAIGNS")},
                ScanIndexForward=False,
                Limit=offset + limit,
            )
        except ClientError:
            raise RepositoryFailure("campaign listing unavailable") from None
        aggregates = [
            CampaignAggregateMetadata.model_validate(_model_payload(_unmarshal_item(item), CampaignAggregateMetadata))
            for item in response.get("Items", [])[offset:]
        ]
        records: list[tuple[CampaignAggregateMetadata, CampaignVersion]] = []
        for aggregate in aggregates:
            record = await self.get(aggregate.campaign_id)
            if record is not None:
                records.append(record)
        return records

    async def exists(self, campaign_id: UUID) -> bool:
        return bool(await self._get_item(campaign_id, meta_sk()))

    async def replace_current(self, aggregate: CampaignAggregateMetadata, version: CampaignVersion) -> None:
        expected_meta_lock = aggregate.lock_version - 1
        expected_version_lock = version.lock_version - 1
        if expected_meta_lock < 0 or expected_version_lock < 0:
            raise InvalidStateTransition("lock version must advance by one")
        values = {
            ":status": _SERIALIZER.serialize(version.status.value),
            ":progress": _SERIALIZER.serialize(version.progress_percent),
            ":updated": _SERIALIZER.serialize(version.updated_at.isoformat().replace("+00:00", "Z")),
            ":new_lock": _SERIALIZER.serialize(version.lock_version),
            ":old_meta_lock": _SERIALIZER.serialize(expected_meta_lock),
            ":old_version_lock": _SERIALIZER.serialize(expected_version_lock),
            ":version": _SERIALIZER.serialize(version.campaign_version),
            ":gsi2pk": _SERIALIZER.serialize(f"STATUS#{version.status.value}"),
            ":gsi2sk": _SERIALIZER.serialize(f"{version.updated_at.isoformat()}#{version.campaign_id}"),
        }
        transaction = [
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _marshal_item({"PK": pk(aggregate.campaign_id), "SK": meta_sk()}),
                    "UpdateExpression": (
                        "SET current_status=:status, current_progress=:progress, updated_at=:updated, "
                        "lock_version=:new_lock, GSI2PK=:gsi2pk, GSI2SK=:gsi2sk"
                    ),
                    "ConditionExpression": "lock_version=:old_meta_lock AND current_version=:version",
                    "ExpressionAttributeValues": {
                        key: values[key]
                        for key in (
                            ":status",
                            ":progress",
                            ":updated",
                            ":new_lock",
                            ":old_meta_lock",
                            ":version",
                            ":gsi2pk",
                            ":gsi2sk",
                        )
                    },
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _marshal_item({"PK": pk(version.campaign_id), "SK": version_sk(version.campaign_version)}),
                    "UpdateExpression": (
                        "SET #status=:status, progress_percent=:progress, updated_at=:updated, lock_version=:new_lock"
                    ),
                    "ConditionExpression": "lock_version=:old_version_lock",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        key: values[key]
                        for key in (":status", ":progress", ":updated", ":new_lock", ":old_version_lock")
                    },
                }
            },
        ]
        try:
            await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
        except ClientError as exc:
            if self._is_conditional(exc):
                raise InvalidStateTransition("campaign optimistic-lock conflict") from None
            raise RepositoryFailure("campaign update unavailable") from None

    async def rollback_initial(self, campaign_id: UUID) -> None:
        transaction = [
            {
                "Delete": {
                    "TableName": self._table_name,
                    "Key": _marshal_item({"PK": pk(campaign_id), "SK": meta_sk()}),
                    "ConditionExpression": "lock_version=:zero AND current_status=:created",
                    "ExpressionAttributeValues": {
                        ":zero": _SERIALIZER.serialize(0),
                        ":created": _SERIALIZER.serialize("CREATED"),
                    },
                }
            },
            {
                "Delete": {
                    "TableName": self._table_name,
                    "Key": _marshal_item({"PK": pk(campaign_id), "SK": version_sk(1)}),
                    "ConditionExpression": "lock_version=:zero AND #status=:created",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":zero": _SERIALIZER.serialize(0),
                        ":created": _SERIALIZER.serialize("CREATED"),
                    },
                }
            },
        ]
        try:
            await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
        except ClientError as exc:
            if self._is_conditional(exc):
                raise InvalidStateTransition("initial campaign is no longer rollback-safe") from None
            raise RepositoryFailure("campaign rollback unavailable") from None

    async def acquire_processing_lease(
        self, campaign_id: UUID, version: int, owner: str, now: datetime, expires_at: datetime
    ) -> None:
        values = {
            ":version": _SERIALIZER.serialize(version),
            ":owner": _SERIALIZER.serialize(owner),
            ":now": _SERIALIZER.serialize(now.isoformat().replace("+00:00", "Z")),
            ":expires": _SERIALIZER.serialize(expires_at.isoformat().replace("+00:00", "Z")),
            ":one": _SERIALIZER.serialize(1),
        }
        transaction = [
            {
                "ConditionCheck": {
                    "TableName": self._table_name,
                    "Key": _marshal_item({"PK": pk(campaign_id), "SK": meta_sk()}),
                    "ConditionExpression": "current_version=:version",
                    "ExpressionAttributeValues": {":version": values[":version"]},
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _marshal_item({"PK": pk(campaign_id), "SK": version_sk(version)}),
                    "UpdateExpression": (
                        "SET lease_owner=:owner, lease_acquired_at=:now, lease_expires_at=:expires, "
                        "lease_heartbeat_at=:now ADD lock_version :one"
                    ),
                    "ConditionExpression": (
                        "attribute_not_exists(lease_expires_at) OR lease_expires_at < :now OR lease_owner=:owner"
                    ),
                    "ExpressionAttributeValues": {key: values[key] for key in (":owner", ":now", ":expires", ":one")},
                }
            },
        ]
        await self._lease_transaction(transaction, "processing lease conflict")

    async def heartbeat_processing_lease(
        self,
        campaign_id: UUID,
        version: int,
        owner: str,
        expected_lock_version: int,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        values = {
            ":owner": _SERIALIZER.serialize(owner),
            ":lock": _SERIALIZER.serialize(expected_lock_version),
            ":now": _SERIALIZER.serialize(now.isoformat().replace("+00:00", "Z")),
            ":expires": _SERIALIZER.serialize(expires_at.isoformat().replace("+00:00", "Z")),
            ":one": _SERIALIZER.serialize(1),
        }
        try:
            await asyncio.to_thread(
                self._client.update_item,
                TableName=self._table_name,
                Key=_marshal_item({"PK": pk(campaign_id), "SK": version_sk(version)}),
                UpdateExpression="SET lease_heartbeat_at=:now, lease_expires_at=:expires ADD lock_version :one",
                ConditionExpression="lease_owner=:owner AND lease_expires_at>=:now AND lock_version=:lock",
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if self._is_conditional(exc):
                raise InvalidStateTransition("processing lease heartbeat conflict") from None
            raise RepositoryFailure("processing lease update unavailable") from None

    async def available(self) -> bool:
        try:
            response = await asyncio.to_thread(self._client.describe_table, TableName=self._table_name)
            return response.get("Table", {}).get("TableStatus") in {"ACTIVE", "UPDATING"}
        except ClientError:
            return False

    async def clear(self) -> None:
        raise RepositoryFailure("DynamoDB repository cleanup is intentionally unsupported")

    async def _get_item(self, campaign_id: UUID, sort_key: str) -> dict[str, Any] | None:
        try:
            response = await asyncio.to_thread(
                self._client.get_item,
                TableName=self._table_name,
                Key=_marshal_item({"PK": pk(campaign_id), "SK": sort_key}),
                ConsistentRead=True,
            )
        except ClientError:
            raise RepositoryFailure("campaign read unavailable") from None
        raw = response.get("Item")
        return None if raw is None else _unmarshal_item(raw)

    async def _lease_transaction(self, transaction: builtins.list[dict[str, Any]], conflict_message: str) -> None:
        try:
            await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
        except ClientError as exc:
            if self._is_conditional(exc):
                raise InvalidStateTransition(conflict_message) from None
            raise RepositoryFailure("processing lease unavailable") from None

    @staticmethod
    def _is_conditional(exc: ClientError) -> bool:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            return True
        if code != "TransactionCanceledException":
            return False
        reasons = exc.response.get("CancellationReasons", [])
        return not reasons or any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)
