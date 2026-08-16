import math

# Calibrated from a real Polly neural-engine production measurement: a 3-scene,
# 28-word narration (2 inter-scene <break> pauses) produced 12.36s of measured
# audio. Backing out the 0.7s of breaks leaves 11.66s of speech for 28 words,
# i.e. ~2.4 words/second. This is an approximation -- Polly's real pace varies
# with punctuation and content -- which is exactly why the target band below
# is narrower than the hard campaign duration constraint, leaving margin for
# estimation error.
WORDS_PER_SECOND = 2.4

SCENE_COUNT = 3
SSML_BREAK_MS = 350
BREAK_SECONDS_TOTAL = (SCENE_COUNT - 1) * (SSML_BREAK_MS / 1000)

# Preferred inner band. Narrower than the hard 13-17s campaign constraint
# (video/pipeline.py's _scaled_scene_durations, left unchanged), so
# word-count estimation error still lands inside the hard bounds.
TARGET_MIN_SECONDS = 14.0
TARGET_MAX_SECONDS = 16.0


def word_count(text: str) -> int:
    return len(text.split())


def estimate_seconds(total_words: int) -> float:
    """Estimate total narration duration, including inter-scene breaks, for
    a given combined word count across all scenes."""
    return total_words / WORDS_PER_SECOND + BREAK_SECONDS_TOTAL


def target_word_range() -> tuple[int, int]:
    """Combined-word-count range across all scenes expected to land inside
    the preferred (TARGET_MIN_SECONDS, TARGET_MAX_SECONDS) band."""
    min_words = math.ceil((TARGET_MIN_SECONDS - BREAK_SECONDS_TOTAL) * WORDS_PER_SECOND)
    max_words = math.floor((TARGET_MAX_SECONDS - BREAK_SECONDS_TOTAL) * WORDS_PER_SECOND)
    return min_words, max_words
