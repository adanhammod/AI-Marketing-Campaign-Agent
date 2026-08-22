from campaign_worker.audio import narration_timing as timing


def test_word_count_splits_on_whitespace():
    assert timing.word_count("Meet Luna Cold Brew, made by Luna Coffee.") == 8


def test_word_count_of_empty_string_is_zero():
    assert timing.word_count("") == 0


def test_estimate_seconds_matches_real_production_measurement():
    # Calibration source: a real 3-scene, 28-word narration (2 inter-scene
    # 350ms <break>s) produced 12.36s of measured Polly audio.
    estimated = timing.estimate_seconds(28)
    assert abs(estimated - 12.36) < 0.05


def test_target_word_range_is_narrower_than_hard_campaign_constraint():
    min_words, max_words = timing.target_word_range()
    # Hard constraint (CampaignConstraints.min/max_duration_seconds,
    # video/pipeline.py._scaled_scene_durations) is 13-20s. The target band
    # must estimate strictly inside it, leaving margin for estimation error.
    assert timing.estimate_seconds(min_words) >= 13.0
    assert timing.estimate_seconds(max_words) <= 20.0
    assert min_words < max_words


def test_target_word_range_matches_calibrated_constants():
    min_words, max_words = timing.target_word_range()
    assert min_words == 32
    assert max_words == 36


def test_shrink_trigger_word_count_leaves_margin_below_the_hard_max():
    # Narration estimated up to (but not exceeding) the trigger must be left
    # alone by _apply_duration_budget's shrink path -- the trigger itself
    # must sit strictly below the hard max, by SHRINK_MARGIN_SECONDS.
    trigger = timing.shrink_trigger_word_count(20.0)
    assert timing.estimate_seconds(trigger) <= 20.0 - timing.SHRINK_MARGIN_SECONDS
    # And well above the preferred TARGET_MAX_SECONDS ceiling -- narration
    # between the ideal band and the hard max is valid, not "too long".
    _, target_max_words = timing.target_word_range()
    assert trigger > target_max_words


def test_shrink_trigger_word_count_derives_from_the_passed_in_hard_max():
    # No duplicated/hardcoded hard-max literal in this module -- the trigger
    # must move if the caller passes a different constraint value.
    assert timing.shrink_trigger_word_count(20.0) > timing.shrink_trigger_word_count(17.0)
