import hashlib

from campaign_worker.errors import WorkflowOperationError

from .models import NormalizedAudio


class AudioProcessor:
    def __init__(self, max_bytes: int = 5_000_000) -> None:
        self.max_bytes = max_bytes

    def validate(self, source: bytes) -> NormalizedAudio:
        if not source:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "voice provider returned empty audio", retryable=True
            )
        if len(source) > self.max_bytes:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED", "synthesized audio exceeds size limit", retryable=False
            )
        if not self._looks_like_mp3(source):
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "voice provider returned non-MP3 audio", retryable=True
            )
        return NormalizedAudio(data=source, checksum_sha256=hashlib.sha256(source).hexdigest())

    @staticmethod
    def _looks_like_mp3(source: bytes) -> bool:
        if source[:3] == b"ID3":
            return True
        return len(source) >= 2 and source[0] == 0xFF and (source[1] & 0xE0) == 0xE0
