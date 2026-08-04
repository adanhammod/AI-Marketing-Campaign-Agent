import asyncio
import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.dynamodb import meta_sk, pk, serialize_step, serialize_version, step_sk, version_sk
from campaign_contracts.enums import WorkflowStep
from campaign_contracts.sqs import SQSJobMessage, duplicate_delivery_key
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.errors import LeaseConflict, LeaseLost, PersistenceUnavailable

from .workflow_repository import LeaseContext, WorkflowRepository

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def _marshal(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _SERIALIZER.serialize(item) for key, item in value.items()}


def _unmarshal(value: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], {key: _DESERIALIZER.deserialize(item) for key, item in value.items()})


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _completion_key(message: SQSJobMessage) -> str:
    campaign_id, campaign_version, operation, idempotency_key = duplicate_delivery_key(message)
    boundary = f"{campaign_id}:{campaign_version}:{operation.value}:{idempotency_key}"
    return "IDEMPOTENCY#WORKER#" + hashlib.sha256(boundary.encode()).hexdigest()


class DynamoDBWorkflowRepository(WorkflowRepository):
    def __init__(self, client: Any, table_name: str) -> None:
        if not table_name:
            raise ValueError("table name is required")
        self._client = client
        self._table_name = table_name

    async def load_version(self, message: SQSJobMessage) -> CampaignVersion | None:
        item = await self._get(pk(message.campaign_id), version_sk(message.campaign_version))
        if item is None:
            return None
        accepted = set(CampaignVersion.model_fields)
        accepted.update(field.alias for field in CampaignVersion.model_fields.values() if field.alias)
        return CampaignVersion.model_validate({key: value for key, value in item.items() if key in accepted})

    async def acquire_lease(
        self, message: SQSJobMessage, owner: str, now: datetime, expires_at: datetime
    ) -> LeaseContext:
        values = {
            ":version": _SERIALIZER.serialize(message.campaign_version),
            ":job": _SERIALIZER.serialize(str(message.job_id)),
            ":owner": _SERIALIZER.serialize(owner),
            ":operation": _SERIALIZER.serialize(message.operation.value),
            ":now": _SERIALIZER.serialize(_iso(now)),
            ":expires": _SERIALIZER.serialize(_iso(expires_at)),
            ":one": _SERIALIZER.serialize(1),
        }
        transaction = [
            {
                "ConditionCheck": {
                    "TableName": self._table_name,
                    "Key": _marshal({"PK": pk(message.campaign_id), "SK": meta_sk()}),
                    "ConditionExpression": "current_version=:version",
                    "ExpressionAttributeValues": {":version": values[":version"]},
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _marshal({"PK": pk(message.campaign_id), "SK": version_sk(message.campaign_version)}),
                    "UpdateExpression": (
                        "SET lease_owner=:owner, lease_acquired_at=:now, lease_expires_at=:expires, "
                        "lease_heartbeat_at=:now, lease_job_id=:job, lease_operation=:operation ADD lock_version :one"
                    ),
                    "ConditionExpression": (
                        "job_id=:job AND (attribute_not_exists(lease_expires_at) OR lease_expires_at<:now OR "
                        "lease_owner=:owner)"
                    ),
                    "ExpressionAttributeValues": {
                        key: values[key] for key in (":job", ":owner", ":operation", ":now", ":expires", ":one")
                    },
                }
            },
        ]
        try:
            await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
        except ClientError as exc:
            if self._conditional(exc):
                raise LeaseConflict("processing lease unavailable") from None
            raise PersistenceUnavailable("lease persistence unavailable") from None
        item = await self._get(pk(message.campaign_id), version_sk(message.campaign_version))
        if item is None:
            raise PersistenceUnavailable("campaign version unavailable")
        return LeaseContext(owner=owner, lock_version=int(item["lock_version"]), expires_at=expires_at)

    async def heartbeat(
        self, message: SQSJobMessage, lease: LeaseContext, now: datetime, expires_at: datetime
    ) -> LeaseContext:
        try:
            response = await asyncio.to_thread(
                self._client.update_item,
                TableName=self._table_name,
                Key=_marshal({"PK": pk(message.campaign_id), "SK": version_sk(message.campaign_version)}),
                UpdateExpression="SET lease_heartbeat_at=:now, lease_expires_at=:expires ADD lock_version :one",
                ConditionExpression=(
                    "lease_owner=:owner AND lease_job_id=:job AND lease_expires_at>=:now AND lock_version=:lock"
                ),
                ExpressionAttributeValues={
                    ":owner": _SERIALIZER.serialize(lease.owner),
                    ":job": _SERIALIZER.serialize(str(message.job_id)),
                    ":now": _SERIALIZER.serialize(_iso(now)),
                    ":expires": _SERIALIZER.serialize(_iso(expires_at)),
                    ":lock": _SERIALIZER.serialize(lease.lock_version),
                    ":one": _SERIALIZER.serialize(1),
                },
                ReturnValues="UPDATED_NEW",
            )
        except ClientError as exc:
            if self._conditional(exc):
                raise LeaseLost("processing lease lost") from None
            raise PersistenceUnavailable("lease heartbeat unavailable") from None
        lock = int(_DESERIALIZER.deserialize(response["Attributes"]["lock_version"]))
        return LeaseContext(owner=lease.owner, lock_version=lock, expires_at=expires_at)

    async def is_completed(self, message: SQSJobMessage) -> bool:
        return await self._get(pk(message.campaign_id), _completion_key(message)) is not None

    async def complete(self, message: SQSJobMessage, lease: LeaseContext, completed_at: datetime) -> None:
        marker = {
            "PK": pk(message.campaign_id),
            "SK": _completion_key(message),
            "entity_type": "IDEMPOTENCY",
            "campaign_id": str(message.campaign_id),
            "campaign_version": Decimal(message.campaign_version),
            "job_id": str(message.job_id),
            "operation": message.operation.value,
            "idempotency_key_hash": hashlib.sha256(message.idempotency_key.encode()).hexdigest(),
            "result": "NO_OP_CHECKPOINT_COMMITTED",
            "completed_at": _iso(completed_at),
        }
        transaction = [
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": _marshal(marker),
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _marshal({"PK": pk(message.campaign_id), "SK": version_sk(message.campaign_version)}),
                    "UpdateExpression": (
                        "REMOVE lease_owner, lease_acquired_at, lease_expires_at, lease_heartbeat_at, "
                        "lease_job_id, lease_operation ADD checkpoint_version :one, lock_version :one"
                    ),
                    "ConditionExpression": "lease_owner=:owner AND lease_job_id=:job AND lock_version=:lock",
                    "ExpressionAttributeValues": {
                        ":owner": _SERIALIZER.serialize(lease.owner),
                        ":job": _SERIALIZER.serialize(str(message.job_id)),
                        ":lock": _SERIALIZER.serialize(lease.lock_version),
                        ":one": _SERIALIZER.serialize(1),
                    },
                }
            },
        ]
        try:
            await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
        except ClientError as exc:
            if self._conditional(exc):
                raise LeaseLost("completion checkpoint conflict") from None
            raise PersistenceUnavailable("completion persistence unavailable") from None

    async def release(self, message: SQSJobMessage, lease: LeaseContext) -> None:
        try:
            await asyncio.to_thread(
                self._client.update_item,
                TableName=self._table_name,
                Key=_marshal({"PK": pk(message.campaign_id), "SK": version_sk(message.campaign_version)}),
                UpdateExpression=(
                    "REMOVE lease_owner, lease_acquired_at, lease_expires_at, lease_heartbeat_at, "
                    "lease_job_id, lease_operation ADD lock_version :one"
                ),
                ConditionExpression="lease_owner=:owner AND lease_job_id=:job AND lock_version=:lock",
                ExpressionAttributeValues={
                    ":owner": _SERIALIZER.serialize(lease.owner),
                    ":job": _SERIALIZER.serialize(str(message.job_id)),
                    ":lock": _SERIALIZER.serialize(lease.lock_version),
                    ":one": _SERIALIZER.serialize(1),
                },
            )
        except ClientError as exc:
            if self._conditional(exc):
                raise LeaseLost("processing lease release conflict") from None
            raise PersistenceUnavailable("lease release unavailable") from None

    async def record_exhausted(self, message: SQSJobMessage, receive_count: int, now: datetime) -> None:
        item = {
            "PK": pk(message.campaign_id),
            "SK": _completion_key(message) + "#EXHAUSTED",
            "entity_type": "DELIVERY_FAILURE",
            "campaign_id": str(message.campaign_id),
            "campaign_version": Decimal(message.campaign_version),
            "job_id": str(message.job_id),
            "operation": message.operation.value,
            "code": "RETRY_EXHAUSTED",
            "receive_count_observed": Decimal(receive_count),
            "recorded_at": _iso(now),
        }
        try:
            await asyncio.to_thread(
                self._client.put_item,
                TableName=self._table_name,
                Item=_marshal(item),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except ClientError as exc:
            if not self._conditional(exc):
                raise PersistenceUnavailable("failure persistence unavailable") from None

    async def record_invalid(self, campaign_id: UUID, code: str, message_id: str, now: datetime) -> None:
        item = {
            "PK": pk(campaign_id),
            "SK": f"INVALID#{code}#{message_id}",
            "entity_type": "INVALID_MESSAGE",
            "campaign_id": str(campaign_id),
            "code": code,
            "recorded_at": _iso(now),
        }
        try:
            await asyncio.to_thread(
                self._client.put_item,
                TableName=self._table_name,
                Item=_marshal(item),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except ClientError as exc:
            if not self._conditional(exc):
                raise PersistenceUnavailable("invalid message persistence unavailable") from None

    async def available(self) -> bool:
        try:
            response = await asyncio.to_thread(self._client.describe_table, TableName=self._table_name)
            return response.get("Table", {}).get("TableStatus") in {"ACTIVE", "UPDATING"}
        except ClientError:
            return False

    async def get_step(self, campaign_id: UUID, campaign_version: int, step: WorkflowStep) -> WorkflowStepRecord | None:
        item = await self._get(pk(campaign_id), step_sk(campaign_version, step))
        if item is None:
            return None
        accepted = set(WorkflowStepRecord.model_fields)
        return WorkflowStepRecord.model_validate({key: value for key, value in item.items() if key in accepted})

    async def save_step(self, record: WorkflowStepRecord) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_item,
                TableName=self._table_name,
                Item=_marshal(serialize_step(record)),
            )
        except ClientError as exc:
            raise PersistenceUnavailable("step persistence unavailable") from exc

    async def save_version(self, version: CampaignVersion, lease: LeaseContext) -> None:
        # lock_version and checkpoint_version are owned by the lease/completion bookkeeping
        # (acquire_lease/heartbeat/complete mutate them independently, concurrently, on this
        # same item) -- content saves must never overwrite them, only the version's own fields.
        body = {
            key: value
            for key, value in serialize_version(version).items()
            if key not in ("PK", "SK", "lock_version", "checkpoint_version")
        }
        marshaled = _marshal(body)
        keys = list(marshaled)
        names = {f"#a{i}": key for i, key in enumerate(keys)}
        values = {f":a{i}": marshaled[key] for i, key in enumerate(keys)}
        values[":owner"] = _SERIALIZER.serialize(lease.owner)
        values[":lock"] = _SERIALIZER.serialize(lease.lock_version)
        set_expression = "SET " + ", ".join(f"{name}=:a{i}" for i, name in enumerate(names))
        try:
            await asyncio.to_thread(
                self._client.update_item,
                TableName=self._table_name,
                Key=_marshal({"PK": pk(version.campaign_id), "SK": version_sk(version.campaign_version)}),
                UpdateExpression=set_expression,
                ConditionExpression="lease_owner=:owner AND lock_version=:lock",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if self._conditional(exc):
                raise LeaseLost("campaign version save conflict") from None
            raise PersistenceUnavailable("version persistence unavailable") from None

    async def _get(self, partition: str, sort: str) -> dict[str, Any] | None:
        try:
            response = await asyncio.to_thread(
                self._client.get_item,
                TableName=self._table_name,
                Key=_marshal({"PK": partition, "SK": sort}),
                ConsistentRead=True,
            )
        except ClientError:
            raise PersistenceUnavailable("workflow state unavailable") from None
        item = response.get("Item")
        return None if item is None else _unmarshal(item)

    @staticmethod
    def _conditional(exc: ClientError) -> bool:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            return True
        if code != "TransactionCanceledException":
            return False
        reasons = exc.response.get("CancellationReasons", [])
        return not reasons or any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)
