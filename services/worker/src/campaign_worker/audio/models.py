from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormalizedAudio:
    data: bytes
    checksum_sha256: str
