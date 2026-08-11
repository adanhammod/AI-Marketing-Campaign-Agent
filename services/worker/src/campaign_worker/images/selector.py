from .models import PhotoCandidate

_TARGET_RATIO = 9 / 16


def rank_candidates(candidates: list[PhotoCandidate], used_ids: set[int]) -> list[PhotoCandidate]:
    eligible = [candidate for candidate in candidates if candidate.photo_id not in used_ids]
    return sorted(
        eligible,
        key=lambda candidate: (
            candidate.width >= candidate.height,
            abs((candidate.width / candidate.height) - _TARGET_RATIO),
            -(candidate.width * candidate.height),
            candidate.photo_id,
        ),
    )
