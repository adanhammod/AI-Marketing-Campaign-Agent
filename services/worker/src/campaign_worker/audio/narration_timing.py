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

# Preferred inner band. Narrower than the hard 13-20s campaign constraint
# (video/pipeline.py's _scaled_scene_durations, left unchanged), so
# word-count estimation error still lands inside the hard bounds. Only the
# *short* side of this band drives generation-time correction (extending
# narration estimated below TARGET_MIN_SECONDS toward it) -- narration
# estimated between this band and the hard max is valid and left alone; see
# shrink_trigger_word_count for the (much higher, hard-max-relative)
# threshold that actually triggers shortening.
TARGET_MIN_SECONDS = 14.0
TARGET_MAX_SECONDS = 16.0

# Headroom kept below the campaign's actual hard max (13-20s, sourced from
# CampaignConstraints.max_duration_seconds -- never duplicated as a literal
# here) before narration is considered "unnecessarily long" and optional
# filler gets dropped. Deliberately well short of the hard max itself: the
# word-count estimate is approximate (see WORDS_PER_SECOND above), and
# narration between the ideal band and the hard max is legitimately valid
# and must not be aggressively shortened back toward the 15s target.
SHRINK_MARGIN_SECONDS = 2.0


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


def shrink_trigger_word_count(hard_max_seconds: float) -> int:
    """Combined word count above which narration is treated as
    "unnecessarily long" and optional/filler content should be dropped.
    Computed from the campaign's actual hard max (not a duplicated
    constant), minus SHRINK_MARGIN_SECONDS of estimation-error headroom --
    narration estimated below this is left completely untouched, even if
    it's already above the preferred TARGET_MAX_SECONDS band."""
    return math.floor((hard_max_seconds - SHRINK_MARGIN_SECONDS - BREAK_SECONDS_TOTAL) * WORDS_PER_SECOND)
