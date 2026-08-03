from dataclasses import dataclass

from campaign_contracts.sqs import SQSJobMessage


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    message_id: str
    receipt_handle: str
    receive_count: int
    job: SQSJobMessage
