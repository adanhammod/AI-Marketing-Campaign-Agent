import asyncio
import hashlib
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, NoReturn, cast
from uuid import UUID

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.dynamodb import (
    meta_sk,
    pk,
    serialize_event,
    serialize_step,
    serialize_version,
    step_sk,
    version_sk,
)
from campaign_contracts.enums import WorkflowStep
from campaign_contracts.events import CampaignEvent
from campaign_contracts.sqs import SQSJobMessage, duplicate_delivery_key
from campaign_contracts.steps import WorkflowStepRecord

from campaign_worker.errors import LeaseConflict, LeaseLost, PersistenceUnavailable

from .workflow_repository import LeaseContext, WorkflowRepository

_MAX_EVENT_SEQUENCE_RETRIES = 5

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
        # Held for the same lease.lock as save_version/complete/release below: this
        # worker's own heartbeat and its own version/completion writes share one
        # DynamoDB item's lock_version, so they must never be in flight at once (see
        # LeaseContext.lock's docstring for the self-race this prevents).
        async with lease.lock:
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
        async with lease.lock:
            try:
                await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
            except ClientError as exc:
                if self._conditional(exc):
                    raise LeaseLost("completion checkpoint conflict") from None
                raise PersistenceUnavailable("completion persistence unavailable") from None

    async def release(self, message: SQSJobMessage, lease: LeaseContext) -> None:
        async with lease.lock:
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

    async def save_step(self, record: WorkflowStepRecord, events: list[CampaignEvent] | None = None) -> None:
        events = events or []
        if not events:
            try:
                await asyncio.to_thread(
                    self._client.put_item,
                    TableName=self._table_name,
                    Item=_marshal(serialize_step(record)),
                )
            except ClientError as exc:
                raise PersistenceUnavailable("step persistence unavailable") from exc
            return
        state_item = {"Put": {"TableName": self._table_name, "Item": _marshal(serialize_step(record))}}
        await self._write_state_with_events(
            campaign_id=record.campaign_id, state_item=state_item, state_conflict=None, events=events
        )

    async def save_version(
        self, version: CampaignVersion, lease: LeaseContext, events: list[CampaignEvent] | None = None
    ) -> None:
        # lock_version and checkpoint_version are owned by the lease/completion bookkeeping
        events = events or []
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
        set_expression = "SET " + ", ".join(f"{name}=:a{i}" for i, name in enumerate(names))
        version_key = _marshal({"PK": pk(version.campaign_id), "SK": version_sk(version.campaign_version)})
        # lease.lock_version is read inside the lock, immediately before dispatch: this
        # serializes against heartbeat()/complete()/release() in this same process, which
        # independently advance lock_version on this exact item. Without this, a concurrent
        # heartbeat can commit its own ADD lock_version between this read and this update
        # reaching DynamoDB, turning a healthy, still-owned lease into a spurious LeaseLost
        # (a self-race, not a real takeover -- see LeaseContext.lock's docstring).
        async with lease.lock:
            values[":lock"] = _SERIALIZER.serialize(lease.lock_version)
            if not events:
                try:
                    await asyncio.to_thread(
                        self._client.update_item,
                        TableName=self._table_name,
                        Key=version_key,
                        UpdateExpression=set_expression,
                        ConditionExpression="lease_owner=:owner AND lock_version=:lock",
                        ExpressionAttributeNames=names,
                        ExpressionAttributeValues=values,
                    )
                except ClientError as exc:
                    if self._conditional(exc):
                        raise LeaseLost("campaign version save conflict") from None
                    raise PersistenceUnavailable("version persistence unavailable") from None
                return

            def _raise_lease_lost() -> NoReturn:
                raise LeaseLost("campaign version save conflict")

            state_item = {
                "Update": {
                    "TableName": self._table_name,
                    "Key": version_key,
                    "UpdateExpression": set_expression,
                    "ConditionExpression": "lease_owner=:owner AND lock_version=:lock",
                    "ExpressionAttributeNames": names,
                    "ExpressionAttributeValues": values,
                }
            }
            await self._write_state_with_events(
                campaign_id=version.campaign_id,
                state_item=state_item,
                state_conflict=_raise_lease_lost,
                events=events,
            )

    async def _read_event_sequence(self, campaign_id: UUID) -> int:
        item = await self._get(pk(campaign_id), meta_sk())
        if item is None:
            raise PersistenceUnavailable("campaign metadata unavailable")
        return int(item.get("event_sequence", 0))

    async def _event_exists(self, campaign_id: UUID, event_id: UUID) -> bool:
        exclusive_start_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "TableName": self._table_name,
                "KeyConditionExpression": "PK=:pk AND begins_with(SK,:event_prefix)",
                "FilterExpression": "event_id=:event_id",
                "ExpressionAttributeValues": {
                    ":pk": _SERIALIZER.serialize(pk(campaign_id)),
                    ":event_prefix": _SERIALIZER.serialize("EVENT#"),
                    ":event_id": _SERIALIZER.serialize(str(event_id)),
                },
                "ConsistentRead": True,
                "Limit": 100,
            }
            if exclusive_start_key is not None:
                kwargs["ExclusiveStartKey"] = exclusive_start_key
            try:
                response = await asyncio.to_thread(self._client.query, **kwargs)
            except ClientError:
                raise PersistenceUnavailable("event replay check unavailable") from None
            if response.get("Items"):
                return True
            exclusive_start_key = response.get("LastEvaluatedKey")
            if exclusive_start_key is None:
                return False

    async def _write_state_with_events(
        self,
        *,
        campaign_id: UUID,
        state_item: dict[str, Any],
        state_conflict: Callable[[], NoReturn] | None,
        events: list[CampaignEvent],
    ) -> None:
        """Appends events transactionally with a state mutation, using an independent
        (Option B) OCC domain for META.event_sequence -- never lock_version, which stays
        exclusively an API-owned optimistic-concurrency field (campaign_api's approve/
        cancel/retry/revise). A conflict purely on event_sequence means another writer
        (the API, or a prior redelivery attempt of this same call) advanced the counter
        first; that is not a real error, so this retries with a freshly read sequence. A
        conflict purely on one of the event Put's deterministic-id condition means the
        exact same logical event was already durably recorded (redelivery/replay), so the
        whole operation is treated as an already-applied no-op. Any conflict on state_item
        itself (e.g. a stale lease) is a genuine failure and must never be swallowed.
        """
        for _ in range(_MAX_EVENT_SEQUENCE_RETRIES):
            existing = [await self._event_exists(campaign_id, event.event_id) for event in events]
            if any(existing):
                if all(existing):
                    await self._validate_replay_state(state_item, state_conflict)
                    return  # replay: the original atomic state-and-event write already committed
                raise PersistenceUnavailable("partial event replay detected")

            sequence_after = await self._read_event_sequence(campaign_id)
            sequenced = [
                event.model_copy(update={"event_sequence": sequence_after + offset})
                for offset, event in enumerate(events, start=1)
            ]
            meta_item = {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _marshal({"PK": pk(campaign_id), "SK": meta_sk()}),
                    "UpdateExpression": "SET event_sequence=:new_seq",
                    "ConditionExpression": "event_sequence=:old_seq",
                    "ExpressionAttributeValues": {
                        ":new_seq": _SERIALIZER.serialize(sequence_after + len(events)),
                        ":old_seq": _SERIALIZER.serialize(sequence_after),
                    },
                }
            }
            event_items = [
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": _marshal(serialize_event(event)),
                        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
                    }
                }
                for event in sequenced
            ]
            transaction = [state_item, meta_item, *event_items]
            try:
                await asyncio.to_thread(self._client.transact_write_items, TransactItems=transaction)
                return
            except ClientError as exc:
                reasons = self._cancellation_reasons(exc)
                if reasons is None:
                    raise PersistenceUnavailable("event persistence unavailable") from None
                if state_conflict is not None and self._reason_failed(reasons, 0):
                    state_conflict()
                if self._reason_failed(reasons, 1):
                    continue  # event_sequence race -- retry with a freshly read sequence
                failed_event_indexes = [i for i in range(2, len(transaction)) if self._reason_failed(reasons, i)]
                if failed_event_indexes and all(
                    self._reason_matches_event(reasons[i], sequenced[i - 2]) for i in failed_event_indexes
                ):
                    return  # proven matching deterministic event: the atomic write already happened
                raise PersistenceUnavailable("event persistence unavailable") from None
        raise PersistenceUnavailable("event sequence contention exceeded retry budget")

    async def _validate_replay_state(
        self, state_item: dict[str, Any], state_conflict: Callable[[], NoReturn] | None
    ) -> None:
        if state_conflict is None:
            return
        update = state_item.get("Update")
        if not isinstance(update, dict):
            raise PersistenceUnavailable("invalid replay state validation")
        condition = cast(str, update["ConditionExpression"])
        check = {key: update[key] for key in ("TableName", "Key")}
        check["ConditionExpression"] = condition
        names = {key: value for key, value in update.get("ExpressionAttributeNames", {}).items() if key in condition}
        values = {key: value for key, value in update.get("ExpressionAttributeValues", {}).items() if key in condition}
        if names:
            check["ExpressionAttributeNames"] = names
        if values:
            check["ExpressionAttributeValues"] = values
        try:
            await asyncio.to_thread(self._client.transact_write_items, TransactItems=[{"ConditionCheck": check}])
        except ClientError as exc:
            if self._conditional(exc):
                state_conflict()
            raise PersistenceUnavailable("event replay validation unavailable") from None

    @staticmethod
    def _cancellation_reasons(exc: ClientError) -> list[dict[str, Any]] | None:
        code = exc.response.get("Error", {}).get("Code", "")
        if code != "TransactionCanceledException":
            return None
        return cast(list[dict[str, Any]], exc.response.get("CancellationReasons", []))

    @staticmethod
    def _reason_failed(reasons: list[dict[str, Any]], index: int) -> bool:
        return index < len(reasons) and reasons[index].get("Code") == "ConditionalCheckFailed"

    @staticmethod
    def _reason_matches_event(reason: dict[str, Any], event: CampaignEvent) -> bool:
        item = reason.get("Item")
        if not isinstance(item, dict):
            return False
        existing = _unmarshal(item)
        return existing.get("event_id") == str(event.event_id)

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
