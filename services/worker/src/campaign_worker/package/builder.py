import hashlib
import io
import json
import zipfile
from typing import Any

from campaign_contracts.campaign import CampaignCopy, Storyboard, StrategyOutput

from campaign_worker.errors import WorkflowOperationError

from .models import BuiltPackage

_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_SCENE_NUMBERS = (1, 2, 3)


def _root(campaign_version: int) -> str:
    return f"campaign-v{campaign_version}/"


def member_names(campaign_version: int) -> dict[str, Any]:
    root = _root(campaign_version)
    return {
        "strategy": f"{root}strategy.json",
        "copy": f"{root}copy.json",
        "storyboard": f"{root}storyboard.json",
        "images": {number: f"{root}images/scene-{number}.jpg" for number in _SCENE_NUMBERS},
        "audio": f"{root}audio/voiceover.mp3",
        "video": f"{root}video/final.mp4",
        "manifest": f"{root}manifest.json",
    }


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), indent=2).encode()


def _write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build_package(
    *,
    campaign_version: int,
    strategy: StrategyOutput,
    campaign_copy: CampaignCopy,
    storyboard: Storyboard,
    images: list[tuple[int, bytes]],
    audio: bytes | None,
    video: bytes,
    manifest: dict[str, Any],
    max_size_bytes: int,
) -> BuiltPackage:
    scene_numbers = sorted(number for number, _ in images)
    if scene_numbers != list(_SCENE_NUMBERS):
        raise ValueError("package assembly requires images for scenes 1, 2, and 3")

    names = member_names(campaign_version)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        _write_member(archive, names["strategy"], _json_bytes(strategy.model_dump(mode="json")))
        _write_member(archive, names["copy"], _json_bytes(campaign_copy.model_dump(mode="json")))
        _write_member(archive, names["storyboard"], _json_bytes(storyboard.model_dump(mode="json")))
        for number, data in sorted(images, key=lambda item: item[0]):
            _write_member(archive, names["images"][number], data)
        if audio is not None:
            _write_member(archive, names["audio"], audio)
        _write_member(archive, names["video"], video)
        _write_member(archive, names["manifest"], _json_bytes(manifest))

    data = buffer.getvalue()
    if len(data) > max_size_bytes:
        raise WorkflowOperationError(
            "ARTIFACT_VALIDATION_FAILED", "assembled package exceeds size limit", retryable=False
        )
    return BuiltPackage(data=data, checksum_sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
