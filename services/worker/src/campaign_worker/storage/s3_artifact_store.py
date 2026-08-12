import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from campaign_contracts.artifacts import ArtifactAttribution
from campaign_contracts.campaign import CampaignVersion

from campaign_worker.audio.models import NormalizedAudio
from campaign_worker.audio.pipeline import deterministic_voice_artifact_id
from campaign_worker.errors import WorkflowOperationError
from campaign_worker.images.models import NormalizedImage
from campaign_worker.images.pipeline import deterministic_artifact_id

from .artifact_store import StoredAudio, StoredImage


class S3ArtifactStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @staticmethod
    def _attribution(metadata: dict[str, Any]) -> ArtifactAttribution:
        attribution = ArtifactAttribution.model_validate(
            {
                "provider_asset_id": str(metadata["pexels_photo_id"]),
                "creator_name": str(metadata["photographer_name"]),
                "creator_profile_url": str(metadata["photographer_profile_url"]),
                "source_page_url": str(metadata["pexels_photo_page_url"]),
                "provider_url": "https://www.pexels.com",
                "attribution_text": f"Photo by {metadata['photographer_name']} on Pexels",
            }
        )
        allowed_hosts = {"pexels.com", "www.pexels.com"}
        for url in (attribution.creator_profile_url, attribution.source_page_url, attribution.provider_url):
            if url.host not in allowed_hosts:
                raise ValueError("Pexels attribution URL has an unexpected host")
        return attribution

    @staticmethod
    def _prefix(version: CampaignVersion, scene_number: int) -> str:
        return f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/images/scene-{scene_number}"

    def reconcile(self, version: CampaignVersion, scene_number: int, fingerprint: str) -> StoredImage | None:
        prefix = self._prefix(version, scene_number)
        try:
            image = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.jpg")["Body"].read()
            raw_metadata = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.metadata.json")["Body"].read()
            metadata = json.loads(raw_metadata)
            checksum = hashlib.sha256(image).hexdigest()
            if (
                metadata["campaign_id"] != str(version.campaign_id)
                or metadata["campaign_version"] != version.campaign_version
                or metadata["scene_number"] != scene_number
                or metadata["storyboard_fingerprint"] != fingerprint
                or metadata["normalized_checksum"] != checksum
            ):
                return None
            return StoredImage(
                artifact_id=deterministic_artifact_id(version.campaign_id, version.campaign_version, scene_number),
                checksum_sha256=checksum,
                size_bytes=len(image),
                created_at=datetime.fromisoformat(metadata["acquisition_timestamp"]),
                pexels_photo_id=int(metadata["pexels_photo_id"]),
                attribution=self._attribution(metadata),
            )
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise WorkflowOperationError(
                "STORAGE_UNAVAILABLE", "artifact reconciliation unavailable", retryable=True
            ) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        except Exception as exc:
            raise WorkflowOperationError(
                "STORAGE_UNAVAILABLE", "artifact reconciliation unavailable", retryable=True
            ) from exc

    def put(
        self,
        version: CampaignVersion,
        scene_number: int,
        image: NormalizedImage,
        metadata: dict[str, object],
    ) -> StoredImage:
        prefix = self._prefix(version, scene_number)
        created_at = datetime.now(UTC)
        safe_metadata = {**metadata, "acquisition_timestamp": created_at.isoformat()}
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=f"{prefix}.jpg",
                Body=image.data,
                ContentType="image/jpeg",
                ServerSideEncryption="AES256",
            )
            self._client.put_object(
                Bucket=self._bucket,
                Key=f"{prefix}.metadata.json",
                Body=json.dumps(safe_metadata, sort_keys=True, separators=(",", ":")).encode(),
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )
        except Exception as exc:
            raise WorkflowOperationError("STORAGE_UNAVAILABLE", "artifact storage unavailable", retryable=True) from exc
        return StoredImage(
            artifact_id=deterministic_artifact_id(version.campaign_id, version.campaign_version, scene_number),
            checksum_sha256=image.checksum_sha256,
            size_bytes=len(image.data),
            created_at=created_at,
            pexels_photo_id=cast(int, metadata["pexels_photo_id"]),
            attribution=self._attribution(cast(dict[str, Any], metadata)),
        )

    @staticmethod
    def _audio_prefix(version: CampaignVersion) -> str:
        return f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/audio/voiceover"

    def reconcile_audio(self, version: CampaignVersion, fingerprint: str) -> StoredAudio | None:
        prefix = self._audio_prefix(version)
        try:
            audio = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.mp3")["Body"].read()
            raw_metadata = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.metadata.json")["Body"].read()
            metadata = json.loads(raw_metadata)
            checksum = hashlib.sha256(audio).hexdigest()
            if (
                metadata["campaign_id"] != str(version.campaign_id)
                or metadata["campaign_version"] != version.campaign_version
                or metadata["narration_fingerprint"] != fingerprint
                or metadata["checksum_sha256"] != checksum
            ):
                return None
            return StoredAudio(
                artifact_id=deterministic_voice_artifact_id(version.campaign_id, version.campaign_version),
                checksum_sha256=checksum,
                size_bytes=len(audio),
                created_at=datetime.fromisoformat(metadata["acquisition_timestamp"]),
            )
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise WorkflowOperationError(
                "STORAGE_UNAVAILABLE", "artifact reconciliation unavailable", retryable=True
            ) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        except Exception as exc:
            raise WorkflowOperationError(
                "STORAGE_UNAVAILABLE", "artifact reconciliation unavailable", retryable=True
            ) from exc

    def put_audio(
        self,
        version: CampaignVersion,
        audio: NormalizedAudio,
        metadata: dict[str, object],
    ) -> StoredAudio:
        prefix = self._audio_prefix(version)
        created_at = datetime.now(UTC)
        safe_metadata = {**metadata, "acquisition_timestamp": created_at.isoformat()}
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=f"{prefix}.mp3",
                Body=audio.data,
                ContentType="audio/mpeg",
                ServerSideEncryption="AES256",
            )
            self._client.put_object(
                Bucket=self._bucket,
                Key=f"{prefix}.metadata.json",
                Body=json.dumps(safe_metadata, sort_keys=True, separators=(",", ":")).encode(),
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )
        except Exception as exc:
            raise WorkflowOperationError("STORAGE_UNAVAILABLE", "artifact storage unavailable", retryable=True) from exc
        return StoredAudio(
            artifact_id=deterministic_voice_artifact_id(version.campaign_id, version.campaign_version),
            checksum_sha256=audio.checksum_sha256,
            size_bytes=len(audio.data),
            created_at=created_at,
        )
