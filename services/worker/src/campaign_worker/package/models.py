from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BuiltPackage:
    data: bytes
    checksum_sha256: str
    size_bytes: int
