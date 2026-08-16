import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from campaign_contracts.enums import CampaignStatus
from campaign_contracts.sqs import SQSJobMessage
from pydantic import ValidationError

from campaign_worker.config import Settings
from campaign_worker.errors import LeaseConflict, LeaseLost, PersistenceUnavailable, ProcessingUncertain
from campaign_worker.queue.message import ReceivedMessage
from campaign_worker.repositories.workflow_repository import LeaseContext, WorkflowRepository
from campaign_worker.services.job_processor import JobProcessor

_LOG = logging.getLogger(__name__)


class MessageOutcome(StrEnum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVALID = "INVALID"
    LEASE_CONFLICT = "LEASE_CONFLICT"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    UNCERTAIN = "UNCERTAIN"


class SQSConsumer:
    def __init__(
        self,
        client: Any,
        repository: WorkflowRepository,
        processor: JobProcessor,
        settings: Settings,
        worker_id: str | None = None,
    ) -> None:
        settings.validate()
        self._client = client
        self._repository = repository
        self._processor = processor
        self._settings = settings
        self._worker_id = worker_id or f"worker-{uuid4()}"
        self._stopping = asyncio.Event()
        self._active: asyncio.Task[MessageOutcome] | None = None

    async def receive(self) -> list[dict[str, Any]]:
        try:
            response = await asyncio.to_thread(
                self._client.receive_message,
                QueueUrl=self._settings.queue_url,
                MaxNumberOfMessages=self._settings.batch_size,
                WaitTimeSeconds=self._settings.wait_time_seconds,
                VisibilityTimeout=self._settings.visibility_timeout_seconds,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except (ClientError, BotoCoreError) as exc:
            raise ProcessingUncertain("queue receive unavailable") from exc
        return list(response.get("Messages", []))

    @staticmethod
    def parse(raw: dict[str, Any]) -> ReceivedMessage:
        try:
            payload = json.loads(str(raw["Body"]))
            job = SQSJobMessage.model_validate(payload)
            count = max(1, int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1")))
            return ReceivedMessage(
                message_id=str(raw["MessageId"]),
                receipt_handle=str(raw["ReceiptHandle"]),
                receive_count=count,
                job=job,
            )
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise ValueError("invalid queue message") from exc

    @staticmethod
    def _best_effort_invalid_identity(raw: dict[str, Any]) -> tuple[UUID, str] | None:
        try:
            payload = json.loads(str(raw["Body"]))
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        campaign_id_raw = payload.get("campaign_id")
        if not isinstance(campaign_id_raw, str):
            return None
        try:
            campaign_id = UUID(campaign_id_raw)
        except ValueError:
            return None
        schema_version = payload.get("schema_version")
        unsupported = schema_version is not None and schema_version != 1
        code = "UNSUPPORTED_MESSAGE_SCHEMA" if unsupported else "VALIDATION_ERROR"
        return campaign_id, code

    async def _record_invalid_if_identifiable(self, raw: dict[str, Any]) -> None:
        identity = self._best_effort_invalid_identity(raw)
        if identity is None:
            return
        campaign_id, code = identity
        message_id = str(raw.get("MessageId", ""))
        try:
            await self._repository.record_invalid(campaign_id, code, message_id, datetime.now(UTC))
        except PersistenceUnavailable:
            _LOG.warning("invalid_message_record_failed")

    async def process_raw(self, raw: dict[str, Any]) -> MessageOutcome:
        try:
            received = self.parse(raw)
        except ValueError:
            _LOG.warning("invalid_queue_message")
            await self._record_invalid_if_identifiable(raw)
            return MessageOutcome.INVALID
        message = received.job
        if received.receive_count > self._settings.max_delivery_attempts:
            try:
                await self._repository.record_exhausted(message, received.receive_count, datetime.now(UTC))
            except PersistenceUnavailable:
                _LOG.warning("retry_exhaustion_persistence_failed")
                return MessageOutcome.UNCERTAIN
            _LOG.warning(
                "delivery_retry_exhausted",
                extra={"campaign_id": str(message.campaign_id), "job_id": str(message.job_id)},
            )
            return MessageOutcome.RETRY_EXHAUSTED
        version = await self._repository.load_version(message)
        if version is None:
            _LOG.warning(
                "campaign_version_unavailable",
                extra={"campaign_id": str(message.campaign_id), "job_id": str(message.job_id)},
            )
            return MessageOutcome.UNCERTAIN
        if version.status == CampaignStatus.CANCELLED:
            return await self._ack_without_processing(received, message, "cancelled_before_lease")
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._settings.visibility_timeout_seconds - 1)
        try:
            lease = await self._repository.acquire_lease(message, self._worker_id, now, expires)
        except LeaseConflict:
            _LOG.info("lease_conflict", extra={"campaign_id": str(message.campaign_id), "job_id": str(message.job_id)})
            return MessageOutcome.LEASE_CONFLICT
        # Re-check status after acquiring the lease: a cancellation may have landed in
        # the narrow window between the read above and the lease transaction committing.
        revalidated = await self._repository.load_version(message)
        if revalidated is not None and revalidated.status == CampaignStatus.CANCELLED:
            try:
                await self._repository.release(message, lease)
            except (LeaseLost, PersistenceUnavailable):
                return MessageOutcome.UNCERTAIN
            return await self._ack_without_processing(received, message, "cancelled_after_lease")
        if await self._repository.is_completed(message):
            try:
                await self._repository.release(message, lease)
                await self._delete(received.receipt_handle)
            except (LeaseLost, PersistenceUnavailable, ProcessingUncertain):
                return MessageOutcome.UNCERTAIN
            return MessageOutcome.ACKNOWLEDGED
        stop = asyncio.Event()
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_loop(received, lease, stop, lost))
        try:
            result = await self._processor.process(message, version, lease)
            if not result.completed or lost.is_set():
                return MessageOutcome.UNCERTAIN
            stop.set()
            await heartbeat
            if lost.is_set():
                return MessageOutcome.UNCERTAIN
            await self._repository.complete(message, lease, datetime.now(UTC))
        except (LeaseLost, PersistenceUnavailable, ProcessingUncertain):
            return MessageOutcome.UNCERTAIN
        finally:
            stop.set()
            if not heartbeat.done():
                await heartbeat
        try:
            await self._delete(received.receipt_handle)
        except ProcessingUncertain:
            return MessageOutcome.UNCERTAIN
        return MessageOutcome.ACKNOWLEDGED

    async def _ack_without_processing(
        self, received: ReceivedMessage, message: SQSJobMessage, reason: str
    ) -> MessageOutcome:
        _LOG.info(reason, extra={"campaign_id": str(message.campaign_id), "job_id": str(message.job_id)})
        try:
            await self._delete(received.receipt_handle)
        except ProcessingUncertain:
            return MessageOutcome.UNCERTAIN
        return MessageOutcome.ACKNOWLEDGED

    async def _heartbeat_loop(
        self, received: ReceivedMessage, lease: LeaseContext, stop: asyncio.Event, lost: asyncio.Event
    ) -> None:
        failures = 0

        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._settings.heartbeat_interval_seconds)
                return
            except TimeoutError:
                pass
            now = datetime.now(UTC)
            expires = now + timedelta(seconds=self._settings.visibility_timeout_seconds - 1)
            try:
                await asyncio.to_thread(
                    self._client.change_message_visibility,
                    QueueUrl=self._settings.queue_url,
                    ReceiptHandle=received.receipt_handle,
                    VisibilityTimeout=self._settings.visibility_timeout_seconds,
                )
                updated = await self._repository.heartbeat(received.job, lease, now, expires)
                lease.lock_version = updated.lock_version
                lease.expires_at = updated.expires_at
                failures = 0
            except LeaseLost:
                lost.set()
                _LOG.warning(
                    "lease_lost",
                    extra={"campaign_id": str(received.job.campaign_id), "job_id": str(received.job.job_id)},
                )
                return
            except (ClientError, BotoCoreError, PersistenceUnavailable):
                failures += 1
                _LOG.warning(
                    "visibility_heartbeat_failed",
                    extra={"campaign_id": str(received.job.campaign_id), "job_id": str(received.job.job_id)},
                )
                if failures >= 2:
                    lost.set()
                    return

    async def _delete(self, receipt_handle: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_message,
                QueueUrl=self._settings.queue_url,
                ReceiptHandle=receipt_handle,
            )
        except (ClientError, BotoCoreError) as exc:
            raise ProcessingUncertain("message acknowledgement unavailable") from exc

    async def available(self) -> bool:
        try:
            await asyncio.to_thread(
                self._client.get_queue_attributes,
                QueueUrl=self._settings.queue_url,
                AttributeNames=["ApproximateNumberOfMessages"],
            )
        except (ClientError, BotoCoreError):
            return False
        return await self._repository.available()

    async def run_once(self) -> list[MessageOutcome]:
        return [await self.process_raw(raw) for raw in await self.receive()]

    async def run(self) -> None:
        backoff = self._settings.sqs_receive_retry_initial_backoff_seconds
        while not self._stopping.is_set():
            try:
                raws = await self.receive()
            except ProcessingUncertain:
                _LOG.warning("receive_failed_retrying", extra={"backoff_seconds": backoff})
                await self._interruptible_sleep(backoff)
                backoff = min(backoff * 2, self._settings.sqs_receive_retry_max_backoff_seconds)
                continue
            backoff = self._settings.sqs_receive_retry_initial_backoff_seconds
            for raw in raws:
                if self._stopping.is_set():
                    return
                self._active = asyncio.create_task(self.process_raw(raw))
                try:
                    await self._active
                finally:
                    self._active = None

    async def _interruptible_sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def shutdown(self) -> None:
        self._stopping.set()
        active = self._active
        if active is None or active.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(active), timeout=self._settings.shutdown_grace_seconds)
        except TimeoutError:
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
