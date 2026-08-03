import asyncio
from uuid import UUID

from campaign_contracts.sqs import SQSJobMessage

from campaign_api.exceptions import DuplicateJobConflict, QueueSubmissionFailure

from .job_queue import JobQueue, QueueSubmissionResult


class InMemoryJobQueue(JobQueue):
    def __init__(self) -> None:
        self._messages: dict[UUID, SQSJobMessage] = {}
        self._lock = asyncio.Lock()
        self.fail_submissions = False

    async def submit(self, message: SQSJobMessage) -> QueueSubmissionResult:
        async with self._lock:
            if self.fail_submissions:
                raise QueueSubmissionFailure("queue submission failed")
            existing = self._messages.get(message.job_id)
            if existing is not None:
                if existing != message:
                    raise DuplicateJobConflict("job ID reused with different message")
                return QueueSubmissionResult(job_id=message.job_id, accepted=True)
            self._messages[message.job_id] = message.model_copy(deep=True)
            return QueueSubmissionResult(job_id=message.job_id, accepted=True)

    async def available(self) -> bool:
        return True

    def messages(self) -> list[SQSJobMessage]:
        return [x.model_copy(deep=True) for x in self._messages.values()]

    async def clear(self) -> None:
        async with self._lock:
            self._messages.clear()
