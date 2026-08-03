import asyncio
import json
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError  # type: ignore[import-untyped]
from campaign_contracts.sqs import SQSJobMessage

from campaign_api.exceptions import QueueSubmissionAmbiguousFailure, QueueSubmissionFailure

from .job_queue import JobQueue, QueueSubmissionResult


class SQSJobQueue(JobQueue):
    """Async facade over an injected low-level boto3 SQS client."""

    def __init__(self, client: Any, queue_url: str) -> None:
        if not queue_url.strip():
            raise ValueError("SQS queue URL is required")
        self._client = client
        self._queue_url = queue_url

    @staticmethod
    def serialize(message: SQSJobMessage) -> str:
        payload = message.model_dump(mode="json", by_alias=True)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    async def submit(self, message: SQSJobMessage) -> QueueSubmissionResult:
        body = self.serialize(message)
        try:
            response = await asyncio.to_thread(
                self._client.send_message,
                QueueUrl=self._queue_url,
                MessageBody=body,
            )
        except ReadTimeoutError as exc:
            # AWS may have accepted the request before the response timed out.
            raise QueueSubmissionAmbiguousFailure("queue submission outcome is unknown") from exc
        except (ClientError, BotoCoreError) as exc:
            raise QueueSubmissionFailure("queue submission failed") from exc
        if not response.get("MessageId"):
            raise QueueSubmissionFailure("queue submission failed")
        return QueueSubmissionResult(job_id=message.job_id, accepted=True)

    async def available(self) -> bool:
        try:
            await asyncio.to_thread(
                self._client.get_queue_attributes,
                QueueUrl=self._queue_url,
                AttributeNames=["ApproximateNumberOfMessages"],
            )
        except (ClientError, BotoCoreError):
            return False
        return True
