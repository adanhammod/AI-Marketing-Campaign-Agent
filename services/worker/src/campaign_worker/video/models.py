from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderedVideo:
    data: bytes
    checksum_sha256: str
    width: int
    height: int
    duration_seconds: float
    video_codec: str
    audio_codec: str
    fps: int
