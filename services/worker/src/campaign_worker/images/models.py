from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SceneSearchQuery:
    scene_number: int
    query: str


@dataclass(frozen=True, slots=True)
class QueryGenerationResult:
    queries: list[SceneSearchQuery]
    provider: str
    model: str | None
    fallback_used: bool


@dataclass(frozen=True, slots=True)
class PhotoCandidate:
    photo_id: int
    width: int
    height: int
    source_url: str
    photo_page_url: str = ""
    photographer: str = ""
    photographer_id: int | None = None
    photographer_url: str = ""
    alt_text: str = ""
    source_variant: str = "portrait"


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    data: bytes
    width: int
    height: int
    checksum_sha256: str
    source_checksum_sha256: str
