import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from campaign_contracts.artifacts import AudioArtifactReference
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.artifact_store import AudioArtifactStore, StoredAudio

from .processor import AudioProcessor

MAX_NARRATION_CHARS = 3000

_DEFAULT_VOICE_BY_LANGUAGE: dict[str, str] = {
    "en": "Joanna",
    "es": "Lupe",
    "fr": "Lea",
    "de": "Vicki",
    "it": "Bianca",
    "pt": "Camila",
}


def deterministic_voice_artifact_id(campaign_id: UUID, campaign_version: int) -> UUID:
    return uuid5(campaign_id, f"version:{campaign_version}:voiceover")


class VoiceAssetPipeline(Protocol):
    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> AudioArtifactReference: ...


class PollyVoicePipeline:
    def __init__(
        self,
        client: Any,
        store: AudioArtifactStore,
        processor: AudioProcessor,
        *,
        voice_id: str | None = None,
        engine: str = "neural",
    ) -> None:
        self._client = client
        self._store = store
        self._processor = processor
        self._voice_id = voice_id
        self._engine = engine

    async def acquire(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> AudioArtifactReference:
        storyboard = version.storyboard
        if storyboard is None:
            raise ValueError("voiceover acquisition requires a storyboard")
        narration_text = " ".join(scene.narration for scene in storyboard.scenes)
        if len(narration_text) > MAX_NARRATION_CHARS:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED", "narration exceeds the supported synthesis length", retryable=False
            )
        fingerprint = hashlib.sha256(narration_text.encode()).hexdigest()

        reconciled = self._store.reconcile_audio(version, fingerprint)
        if reconciled is not None:
            return self._reference(version, reconciled)

        voice_id = self._resolve_voice_id(version.brief.language)

        await self._checkpoint(is_cancelled, "before_polly")
        audio_bytes = self._synthesize(narration_text, voice_id)
        await self._checkpoint(is_cancelled, "before_validation")
        normalized = self._processor.validate(audio_bytes)
        await self._checkpoint(is_cancelled, "before_s3_upload")
        metadata = {
            "campaign_id": str(version.campaign_id),
            "campaign_version": version.campaign_version,
            "narration_fingerprint": fingerprint,
            "polly_voice_id": voice_id,
            "polly_engine": self._engine,
            "language_code": version.brief.language,
            "checksum_sha256": normalized.checksum_sha256,
        }
        stored = self._store.put_audio(version, normalized, metadata)
        return self._reference(version, stored)

    def _resolve_voice_id(self, language: str) -> str:
        if self._voice_id:
            return self._voice_id
        base = language.split("-")[0].lower()
        voice = _DEFAULT_VOICE_BY_LANGUAGE.get(base)
        if voice is None:
            raise WorkflowOperationError(
                "VOICE_PROVIDER_UNAVAILABLE",
                f"no configured Polly voice for language '{language}'",
                retryable=False,
            )
        return voice

    def _synthesize(self, text: str, voice_id: str) -> bytes:
        try:
            response = self._client.synthesize_speech(
                Text=text, VoiceId=voice_id, Engine=self._engine, OutputFormat="mp3"
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ThrottlingException":
                raise WorkflowOperationError(
                    "PROVIDER_THROTTLED", "Polly throttled the request", retryable=True
                ) from exc
            if code == "ServiceUnavailableException":
                raise WorkflowOperationError(
                    "VOICE_PROVIDER_UNAVAILABLE", "Polly is temporarily unavailable", retryable=True
                ) from exc
            if code in {
                "AccessDeniedException",
                "UnrecognizedClientException",
                "InvalidParameterValueException",
                "InvalidSampleRateException",
                "LanguageNotSupportedException",
                "TextLengthExceededException",
            }:
                raise WorkflowOperationError(
                    "VOICE_PROVIDER_UNAVAILABLE", "Polly rejected the request", retryable=False
                ) from exc
            raise WorkflowOperationError("VOICE_PROVIDER_UNAVAILABLE", "Polly request failed", retryable=True) from exc
        except BotoCoreError as exc:
            raise WorkflowOperationError("PROVIDER_TIMEOUT", "Polly request timed out", retryable=True) from exc
        try:
            return cast(bytes, response["AudioStream"].read())
        except Exception as exc:
            raise WorkflowOperationError(
                "INVALID_PROVIDER_OUTPUT", "Polly returned no audio stream", retryable=True
            ) from exc

    @staticmethod
    async def _checkpoint(is_cancelled: Callable[[], Awaitable[bool]], phase: str) -> None:
        if await is_cancelled():
            raise NodeCancelled(f"generate_voiceover:{phase}")

    @staticmethod
    def _reference(version: CampaignVersion, stored: StoredAudio) -> AudioArtifactReference:
        return AudioArtifactReference(
            artifact_id=stored.artifact_id,
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            workflow_step=WorkflowStep.VOICEOVER,
            mime_type="audio/mpeg",
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.checksum_sha256,
            created_at=stored.created_at,
            provider="polly",
        )
