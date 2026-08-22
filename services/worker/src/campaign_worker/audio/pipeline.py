import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast
from uuid import UUID, uuid5
from xml.sax.saxutils import escape as xml_escape

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from campaign_contracts.artifacts import AudioArtifactReference
from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.graph.boundary import NodeCancelled
from campaign_worker.storage.artifact_store import AudioArtifactStore, StoredAudio
from campaign_worker.video.ffmpeg_runner import run_ffmpeg, run_ffprobe

from .normalizer import AudioNormalizer
from .processor import AudioProcessor
from .tempo_normalizer import ensure_duration_in_range

MAX_NARRATION_CHARS = 3000

# Bump-on-change: any change to how synthesis behaves for a given
# narration+voice+engine (SSML shape, pacing, normalization targets) must
# invalidate previously-reconciled artifacts. Mirrors video/pipeline.py's
# _RENDER_SETTINGS_VERSION precedent. Deliberately kept under the existing
# "narration_fingerprint" metadata key (not renamed) -- reconcile_audio()
# only ever does an opaque string-equality check on that key's value, so
# widening what it hashes is sufficient for correctness without touching
# storage/s3_artifact_store.py or any metadata-dict test fixture's keys.
_VOICE_SYNTHESIS_VERSION = 1

# Fixed pause between each of the 3 scenes' narration, inserted as SSML
# <break> tags. `<break>` has full support on the neural engine; `<prosody>`
# is only partially supported (no pitch) and `<emphasis>` isn't supported at
# all, so pacing is the only prosody lever used in v1.
_SSML_BREAK_MS = 350

_DEFAULT_VOICE_BY_LANGUAGE: dict[str, str] = {
    "en": "Joanna",
    "es": "Lupe",
    "fr": "Lea",
    "de": "Vicki",
    "it": "Bianca",
    "pt": "Camila",
}


def _synthesis_fingerprint(narration_text: str, voice_id: str, engine: str) -> str:
    payload = f"{narration_text}|{voice_id}|{engine}|v{_VOICE_SYNTHESIS_VERSION}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _build_ssml(scene_narrations: list[str]) -> str:
    # Each scene's narration is plain prose (no markup), so escaping each one
    # independently before joining is equivalent to escaping the individual
    # brief/strategy fields it was built from -- and much simpler, since it
    # doesn't require threading escaping logic into graph/nodes.py.
    escaped_scenes = [xml_escape(text) for text in scene_narrations]
    body = f'<break time="{_SSML_BREAK_MS}ms"/>'.join(escaped_scenes)
    return f"<speak>{body}</speak>"


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
        normalizer: AudioNormalizer,
        *,
        voice_id: str | None = None,
        engine: str = "neural",
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        duration_check_timeout_seconds: float = 30,
        ffmpeg_runner: Callable[..., Awaitable[None]] = run_ffmpeg,
        ffprobe_runner: Callable[..., Awaitable[dict[str, Any]]] = run_ffprobe,
    ) -> None:
        self._client = client
        self._store = store
        self._processor = processor
        self._normalizer = normalizer
        self._voice_id = voice_id
        self._engine = engine
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._duration_check_timeout_seconds = duration_check_timeout_seconds
        self._ffmpeg_runner = ffmpeg_runner
        self._ffprobe_runner = ffprobe_runner

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

        voice_id = self._resolve_voice_id(version.brief.language)
        fingerprint = _synthesis_fingerprint(narration_text, voice_id, self._engine)

        reconciled = self._store.reconcile_audio(version, fingerprint)
        if reconciled is not None:
            return self._reference(version, reconciled)

        await self._checkpoint(is_cancelled, "before_polly")
        ssml_text = _build_ssml([scene.narration for scene in storyboard.scenes])
        audio_bytes = self._synthesize(ssml_text, voice_id)
        normalized_loudness = await self._normalizer.normalize(audio_bytes)
        await self._checkpoint(is_cancelled, "before_validation")
        normalized = self._processor.validate(normalized_loudness.data)

        await self._checkpoint(is_cancelled, "before_duration_check")
        # Measures the REAL synthesized duration and, if needed, applies a
        # small bounded tempo correction here -- at the voiceover step, not
        # deferred to video/pipeline.py -- so a duration problem is both
        # caught before the (much more expensive) video render and
        # correctly attributed to WorkflowStep.VOICEOVER via this node's
        # existing with_failure_attribution wrapping.
        duration_result = await ensure_duration_in_range(
            normalized.data,
            min_duration_seconds=version.constraints.min_duration_seconds,
            max_duration_seconds=version.constraints.max_duration_seconds,
            ffmpeg_path=self._ffmpeg_path,
            ffprobe_path=self._ffprobe_path,
            timeout_seconds=self._duration_check_timeout_seconds,
            ffmpeg_runner=self._ffmpeg_runner,
            ffprobe_runner=self._ffprobe_runner,
        )
        if duration_result.tempo_factor is not None:
            # Tempo-shifted bytes are a new encode: re-validate/re-checksum
            # so what's recorded/stored matches what's actually uploaded.
            normalized = self._processor.validate(duration_result.data)

        await self._checkpoint(is_cancelled, "before_s3_upload")
        metadata = {
            "campaign_id": str(version.campaign_id),
            "campaign_version": version.campaign_version,
            "narration_fingerprint": fingerprint,
            "polly_voice_id": voice_id,
            "polly_engine": self._engine,
            "language_code": version.brief.language,
            "checksum_sha256": normalized.checksum_sha256,
            "text_type": "ssml",
            "ssml_break_ms": _SSML_BREAK_MS,
            "synthesis_version": _VOICE_SYNTHESIS_VERSION,
            "normalization_applied": True,
            "target_lufs": self._normalizer.target_lufs,
            "true_peak_ceiling_dbtp": self._normalizer.true_peak_ceiling_dbtp,
            "measured_integrated_lufs": normalized_loudness.measured_integrated_lufs,
            "measured_true_peak_dbtp": normalized_loudness.measured_true_peak_dbtp,
            "measured_duration_seconds": duration_result.measured_duration_seconds,
            "tempo_factor": duration_result.tempo_factor,
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
                Text=text, VoiceId=voice_id, Engine=self._engine, OutputFormat="mp3", TextType="ssml"
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
                # Defense-in-depth alongside per-scene XML escaping (_build_ssml):
                # a malformed-SSML business/product/message field is a
                # deterministic input problem, not a transient provider issue,
                # so it must not be retried.
                "InvalidSsmlException",
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
