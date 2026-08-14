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
from campaign_worker.package.models import BuiltPackage
from campaign_worker.package.pipeline import deterministic_package_artifact_id
from campaign_worker.video.models import RenderedVideo
from campaign_worker.video.pipeline import deterministic_video_artifact_id

from .artifact_store import StoredAudio, StoredImage, StoredPackage, StoredVideo


class S3ArtifactStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @staticmethod
    def _attribution(metadata: dict[str, Any]) -> ArtifactAttribution | None:
        # Generative (Stability) images have no photographer to attribute --
        # only build Pexels attribution when the stock-only metadata key is
        # present, so a KeyError here never gets misread as a cache miss
        # (see reconcile()'s broad except clause below).
        if "pexels_photo_id" not in metadata:
            return None
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
            pexels_photo_id = metadata.get("pexels_photo_id")
            return StoredImage(
                artifact_id=deterministic_artifact_id(version.campaign_id, version.campaign_version, scene_number),
                checksum_sha256=checksum,
                size_bytes=len(image),
                created_at=datetime.fromisoformat(metadata["acquisition_timestamp"]),
                pexels_photo_id=int(pexels_photo_id) if pexels_photo_id is not None else None,
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
        typed_metadata = cast(dict[str, Any], metadata)
        pexels_photo_id = typed_metadata.get("pexels_photo_id")
        return StoredImage(
            artifact_id=deterministic_artifact_id(version.campaign_id, version.campaign_version, scene_number),
            checksum_sha256=image.checksum_sha256,
            size_bytes=len(image.data),
            created_at=created_at,
            pexels_photo_id=int(pexels_photo_id) if pexels_photo_id is not None else None,
            attribution=self._attribution(typed_metadata),
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

    @staticmethod
    def _video_prefix(version: CampaignVersion) -> str:
        return f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/video/final"

    def reconcile_video(self, version: CampaignVersion, fingerprint: str) -> StoredVideo | None:
        prefix = self._video_prefix(version)
        try:
            video = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.mp4")["Body"].read()
            raw_metadata = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.metadata.json")["Body"].read()
            metadata = json.loads(raw_metadata)
            checksum = hashlib.sha256(video).hexdigest()
            if (
                metadata["campaign_id"] != str(version.campaign_id)
                or metadata["campaign_version"] != version.campaign_version
                or metadata["render_fingerprint"] != fingerprint
                or metadata["checksum_sha256"] != checksum
            ):
                return None
            return StoredVideo(
                artifact_id=deterministic_video_artifact_id(version.campaign_id, version.campaign_version),
                checksum_sha256=checksum,
                size_bytes=len(video),
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

    def put_video(
        self,
        version: CampaignVersion,
        video: RenderedVideo,
        metadata: dict[str, object],
    ) -> StoredVideo:
        prefix = self._video_prefix(version)
        created_at = datetime.now(UTC)
        safe_metadata = {**metadata, "acquisition_timestamp": created_at.isoformat()}
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=f"{prefix}.mp4",
                Body=video.data,
                ContentType="video/mp4",
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
        return StoredVideo(
            artifact_id=deterministic_video_artifact_id(version.campaign_id, version.campaign_version),
            checksum_sha256=video.checksum_sha256,
            size_bytes=len(video.data),
            created_at=created_at,
        )

    @staticmethod
    def _package_prefix(version: CampaignVersion) -> str:
        return f"campaigns/{version.campaign_id}/versions/{version.campaign_version}/package/campaign"

    def reconcile_package(self, version: CampaignVersion, fingerprint: str) -> StoredPackage | None:
        prefix = self._package_prefix(version)
        try:
            package = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.zip")["Body"].read()
            raw_metadata = self._client.get_object(Bucket=self._bucket, Key=f"{prefix}.metadata.json")["Body"].read()
            metadata = json.loads(raw_metadata)
            checksum = hashlib.sha256(package).hexdigest()
            if (
                metadata["campaign_id"] != str(version.campaign_id)
                or metadata["campaign_version"] != version.campaign_version
                or metadata["package_fingerprint"] != fingerprint
                or metadata["checksum_sha256"] != checksum
            ):
                return None
            return StoredPackage(
                artifact_id=deterministic_package_artifact_id(version.campaign_id, version.campaign_version),
                checksum_sha256=checksum,
                size_bytes=len(package),
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

    def put_package(
        self,
        version: CampaignVersion,
        package: BuiltPackage,
        metadata: dict[str, object],
    ) -> StoredPackage:
        prefix = self._package_prefix(version)
        created_at = datetime.now(UTC)
        safe_metadata = {**metadata, "acquisition_timestamp": created_at.isoformat()}
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=f"{prefix}.zip",
                Body=package.data,
                ContentType="application/zip",
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
        return StoredPackage(
            artifact_id=deterministic_package_artifact_id(version.campaign_id, version.campaign_version),
            checksum_sha256=package.checksum_sha256,
            size_bytes=package.size_bytes,
            created_at=created_at,
        )
