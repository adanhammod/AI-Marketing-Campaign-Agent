import io
import json
import logging

import pytest
from campaign_contracts.enums import VideoStyle
from pydantic import ValidationError

from campaign_worker.graph.creative_plan_provider import (
    CREATIVE_PLAN_PROMPT_VERSION,
    MAX_SHOTS,
    MIN_SHOTS,
    BedrockCreativePlanProvider,
    CreativePlanValidationError,
    DeterministicCreativePlanProvider,
    FallbackCreativePlanProvider,
    _build_prompt,
    build_creative_plan_request,
)
from campaign_worker.providers.models import CreativePlanRequest, CreativePlanSceneContext


def _request(*, video_style: VideoStyle = VideoStyle.CINEMATIC_TEXT_AD, target_duration_seconds: int = 15) -> CreativePlanRequest:
    return CreativePlanRequest(
        business_name="Example Coffee",
        product_or_service="Cold brew subscription",
        campaign_goal="increase online subscription sales",
        platforms=["instagram"],
        target_audience="Urban professionals",
        tone="bright",
        video_style=video_style,
        strategy_objective="Drive subscriptions",
        strategy_positioning="Premium local roaster",
        key_message="Fresh cold brew, delivered weekly",
        campaign_headline="Example Coffee: Fresh cold brew, delivered weekly",
        call_to_action="Subscribe today",
        scenes=[
            CreativePlanSceneContext(
                scene_number=n, purpose=f"Scene {n}", visual_prompt=f"cold brew scene {n}", narration=f"Narration {n}."
            )
            for n in (1, 2, 3)
        ],
        target_duration_seconds=target_duration_seconds,
    )


def _valid_shot(number, scene, duration, *, role="HOOK", asset_role="HERO_PRODUCT", text=None, camera="STATIC", transition="CUT", audio_cues=None):
    return {
        "shot_number": number,
        "role": role,
        "source_scene_number": scene,
        "asset_role": asset_role,
        "visual_description": "close product crop with strong foreground depth",
        "duration_seconds": duration,
        "text": text,
        "camera_motion": camera,
        "transition_in": transition,
        "audio_cues": audio_cues or [],
    }


def _valid_plan_dict(*, shot_count: int = 6, total: int = 15) -> dict:
    per_shot = total / shot_count
    shots = []
    roles = ["HOOK", "PRODUCT_HERO", "LIFESTYLE", "MESSAGE", "PAYOFF", "CTA", "DETAIL", "ACTION"]
    asset_roles = ["HERO_PRODUCT", "HERO_PRODUCT", "LIFESTYLE_PRODUCT", "LIFESTYLE_PRODUCT", "HERO_PRODUCT", "CTA_FRAME", "DETAIL_SHOT", "ACTION_SHOT"]
    scenes = [1, 1, 2, 2, 3, 3, 2, 1]
    for i in range(shot_count):
        shots.append(
            _valid_shot(
                i + 1,
                scenes[i % len(scenes)],
                per_shot,
                role=roles[i % len(roles)],
                asset_role=asset_roles[i % len(asset_roles)],
                text="SHORT COPY." if i in (0, shot_count - 1) else None,
                transition="CUT" if i % 2 == 0 else "MASK_REVEAL",
                audio_cues=["TRANSITION_HIT"] if i == 1 else [],
            )
        )
    return {
        "concept": "YOUR 3PM RESET",
        "visual_style": "modern, energetic, premium",
        "total_duration_seconds": total,
        "shots": shots,
    }


def _bedrock_response(payload_text: str):
    body = json.dumps({"output": {"message": {"content": [{"text": payload_text}]}}})
    return {"body": io.BytesIO(body.encode())}


class _FakeBedrockClient:
    def __init__(self, response_text: str | None = None, error: Exception | None = None) -> None:
        self._response_text = response_text
        self._error = error
        self.calls = 0
        self.last_kwargs: dict | None = None

    def invoke_model(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return _bedrock_response(self._response_text)


# ---------------------------------------------------------------------------
# DeterministicCreativePlanProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_provider_produces_six_shots_matching_target_duration():
    request = _request()
    plan = await DeterministicCreativePlanProvider().generate(request)
    assert len(plan.shots) == 6
    assert plan.total_duration_seconds == 15
    assert abs(sum(s.duration_seconds for s in plan.shots) - 15) <= 0.01


@pytest.mark.asyncio
async def test_deterministic_provider_is_deterministic():
    request = _request()
    first = await DeterministicCreativePlanProvider().generate(request)
    second = await DeterministicCreativePlanProvider().generate(request)
    assert first == second


# ---------------------------------------------------------------------------
# BedrockCreativePlanProvider -- valid responses (A, B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_six_shot_bedrock_plan_is_accepted():
    client = _FakeBedrockClient(json.dumps(_valid_plan_dict(shot_count=6)))
    provider = BedrockCreativePlanProvider(client, "test-model")
    plan = await provider.generate(_request())
    assert len(plan.shots) == 6
    assert plan.concept == "YOUR 3PM RESET"


@pytest.mark.asyncio
async def test_valid_eight_shot_bedrock_plan_is_accepted():
    client = _FakeBedrockClient(json.dumps(_valid_plan_dict(shot_count=8)))
    provider = BedrockCreativePlanProvider(client, "test-model")
    plan = await provider.generate(_request())
    assert len(plan.shots) == 8


@pytest.mark.asyncio
async def test_valid_seven_shot_bedrock_plan_is_accepted():
    client = _FakeBedrockClient(json.dumps(_valid_plan_dict(shot_count=7)))
    provider = BedrockCreativePlanProvider(client, "test-model")
    plan = await provider.generate(_request())
    assert len(plan.shots) == 7


# ---------------------------------------------------------------------------
# BedrockCreativePlanProvider -- invalid responses (C-H)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_raises():
    client = _FakeBedrockClient("{not valid json")
    provider = BedrockCreativePlanProvider(client, "test-model")
    with pytest.raises(json.JSONDecodeError):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_invalid_enum_raises_validation_error():
    plan_dict = _valid_plan_dict(shot_count=6)
    plan_dict["shots"][0]["camera_motion"] = "ZOOM_WHOOSH_NOT_REAL"
    client = _FakeBedrockClient(json.dumps(plan_dict))
    provider = BedrockCreativePlanProvider(client, "test-model")
    with pytest.raises(ValidationError):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_invalid_duration_sum_raises_validation_error():
    plan_dict = _valid_plan_dict(shot_count=6, total=15)
    plan_dict["shots"][0]["duration_seconds"] = 100.0  # sum no longer matches total
    client = _FakeBedrockClient(json.dumps(plan_dict))
    provider = BedrockCreativePlanProvider(client, "test-model")
    with pytest.raises(ValidationError):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_unsupported_transition_raises_creative_plan_validation_error():
    plan_dict = _valid_plan_dict(shot_count=6)
    plan_dict["shots"][0]["transition_in"] = "WIPE"
    client = _FakeBedrockClient(json.dumps(plan_dict))
    provider = BedrockCreativePlanProvider(client, "test-model")
    with pytest.raises(CreativePlanValidationError):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_invalid_source_scene_number_raises_validation_error():
    plan_dict = _valid_plan_dict(shot_count=6)
    plan_dict["shots"][0]["source_scene_number"] = 4  # only 1-3 exist
    client = _FakeBedrockClient(json.dumps(plan_dict))
    provider = BedrockCreativePlanProvider(client, "test-model")
    with pytest.raises(ValidationError):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_provider_timeout_or_error_propagates():
    client = _FakeBedrockClient(error=TimeoutError("bedrock timed out"))
    provider = BedrockCreativePlanProvider(client, "test-model")
    with pytest.raises(TimeoutError):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_shot_count_outside_bounds_raises_creative_plan_validation_error():
    plan_dict = _valid_plan_dict(shot_count=3, total=15)
    # sequence_and_duration validator on CreativeVideoPlan is independent of
    # the shot-count *range* check (MIN_SHOTS/MAX_SHOTS) -- 3 shots is a
    # perfectly valid CreativeVideoPlan by the shared contract's own rules,
    # so this exercises the provider's additional range check specifically.
    client = _FakeBedrockClient(json.dumps(plan_dict))
    provider = BedrockCreativePlanProvider(client, "test-model")
    with pytest.raises(CreativePlanValidationError):
        await provider.generate(_request())


# ---------------------------------------------------------------------------
# FallbackCreativePlanProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_provider_uses_primary_when_it_succeeds():
    client = _FakeBedrockClient(json.dumps(_valid_plan_dict(shot_count=6)))
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    plan = await fallback.generate(_request())
    assert plan.concept == "YOUR 3PM RESET"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_fallback_provider_falls_back_on_malformed_json():
    client = _FakeBedrockClient("not json at all")
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    request = _request()
    plan = await fallback.generate(request)
    expected = await DeterministicCreativePlanProvider().generate(request)
    assert plan == expected


@pytest.mark.asyncio
async def test_fallback_provider_falls_back_on_invalid_enum():
    plan_dict = _valid_plan_dict(shot_count=6)
    plan_dict["shots"][0]["asset_role"] = "NOT_A_REAL_ROLE"
    client = _FakeBedrockClient(json.dumps(plan_dict))
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    request = _request()
    plan = await fallback.generate(request)
    expected = await DeterministicCreativePlanProvider().generate(request)
    assert plan == expected


@pytest.mark.asyncio
async def test_fallback_provider_falls_back_on_unsupported_transition():
    plan_dict = _valid_plan_dict(shot_count=6)
    plan_dict["shots"][0]["transition_in"] = "MOTION_MATCH"
    client = _FakeBedrockClient(json.dumps(plan_dict))
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    request = _request()
    plan = await fallback.generate(request)
    expected = await DeterministicCreativePlanProvider().generate(request)
    assert plan == expected


@pytest.mark.asyncio
async def test_fallback_provider_falls_back_on_provider_error():
    client = _FakeBedrockClient(error=ConnectionError("bedrock unreachable"))
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    request = _request()
    plan = await fallback.generate(request)
    expected = await DeterministicCreativePlanProvider().generate(request)
    assert plan == expected


@pytest.mark.asyncio
async def test_fallback_output_equals_deterministic_generator_output_for_same_inputs():
    client = _FakeBedrockClient(error=RuntimeError("boom"))
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    request = _request()
    via_fallback = await fallback.generate(request)
    direct = await DeterministicCreativePlanProvider().generate(request)
    assert via_fallback == direct


@pytest.mark.asyncio
async def test_fallback_logs_provider_started_succeeded_and_invalid_and_fallback_used(caplog):
    plan_dict = _valid_plan_dict(shot_count=6)
    plan_dict["shots"][0]["transition_in"] = "WIPE"
    client = _FakeBedrockClient(json.dumps(plan_dict))
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    with caplog.at_level(logging.INFO):
        await fallback.generate(_request())
    events = [record.getMessage() for record in caplog.records]
    assert "creative_plan.provider_started" in events
    assert "creative_plan.provider_invalid" in events
    assert "creative_plan.fallback_used" in events
    assert "creative_plan.provider_succeeded" not in events


@pytest.mark.asyncio
async def test_fallback_logs_only_started_and_succeeded_on_a_clean_primary_success(caplog):
    client = _FakeBedrockClient(json.dumps(_valid_plan_dict(shot_count=6)))
    fallback = FallbackCreativePlanProvider(
        primary=BedrockCreativePlanProvider(client, "test-model"),
        fallback=DeterministicCreativePlanProvider(),
    )
    with caplog.at_level(logging.INFO):
        await fallback.generate(_request())
    events = [record.getMessage() for record in caplog.records]
    assert "creative_plan.provider_started" in events
    assert "creative_plan.provider_succeeded" in events
    assert "creative_plan.fallback_used" not in events
    assert "creative_plan.provider_invalid" not in events


# ---------------------------------------------------------------------------
# build_creative_plan_request
# ---------------------------------------------------------------------------


def test_bounds_are_five_to_eight():
    assert MIN_SHOTS == 5
    assert MAX_SHOTS == 8


def test_prompt_version_is_a_stable_string_constant():
    assert isinstance(CREATIVE_PLAN_PROMPT_VERSION, str)
    assert CREATIVE_PLAN_PROMPT_VERSION


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


def test_prompt_includes_allowed_enum_values():
    prompt = _build_prompt(_request())
    for value in ("HOOK", "PRODUCT_HERO", "ACTION", "DETAIL", "LIFESTYLE", "MESSAGE", "PAYOFF", "CTA"):
        assert value in prompt
    for value in ("HERO_PRODUCT", "ACTION_SHOT", "DETAIL_SHOT", "LIFESTYLE_PRODUCT", "CTA_FRAME"):
        assert value in prompt
    for value in ("STATIC", "PUSH_IN", "PULL_OUT", "PAN_LEFT", "PAN_RIGHT", "PAN_UP", "PAN_DOWN", "MACRO_PUSH", "SCALE_THROUGH"):
        assert value in prompt
    for value in ("TRANSITION_HIT", "IMPACT", "WHOOSH", "BRAND_HIT", "ICE_CLINK"):
        assert value in prompt


def test_prompt_restricts_transitions_to_renderer_supported_set():
    prompt = _build_prompt(_request())
    assert "CUT" in prompt
    assert "MASK_REVEAL" in prompt
    assert "CROSSFADE" in prompt
    # The full TransitionType enum also contains WIPE/SCALE_THROUGH/MOTION_MATCH --
    # those must never appear as allowed values in the prompt.
    assert "WIPE" not in prompt
    assert "MOTION_MATCH" not in prompt


def test_prompt_prohibits_css_gsap_renderer_syntax():
    prompt = _build_prompt(_request())
    lower = prompt.lower()
    assert "css" in lower or "gsap" in lower or "clip-path" in lower
    assert "never css" in lower or "no css" in lower or "never gsap" in lower or "renderer" in lower


def test_prompt_includes_campaign_context():
    request = _request()
    prompt = _build_prompt(request)
    assert request.business_name in prompt
    assert request.product_or_service in prompt
    assert request.call_to_action in prompt
    assert request.key_message in prompt


def test_cinematic_text_ad_prompt_requests_short_text_and_music_first_pacing():
    prompt = _build_prompt(_request(video_style=VideoStyle.CINEMATIC_TEXT_AD))
    lower = prompt.lower()
    assert "music-first" in lower or "music first" in lower
    assert "no voiceover" in lower or "cinematic_text_ad" in lower.replace(" ", "_")


def test_voiceover_ad_prompt_avoids_text_heavy_plan():
    prompt = _build_prompt(_request(video_style=VideoStyle.VOICEOVER_AD))
    lower = prompt.lower()
    assert "voiceover_ad" in lower.replace(" ", "_") or "narration" in lower
    assert "minimal" in lower


# ---------------------------------------------------------------------------
# build_creative_plan_request context correctness
# ---------------------------------------------------------------------------


def test_request_sent_to_provider_carries_no_aws_or_artifact_metadata():
    request = _request()
    dumped = request.model_dump()
    for forbidden in ("campaign_id", "campaign_version", "artifact_id", "s3", "dynamodb", "bucket"):
        assert forbidden not in dumped


# ---------------------------------------------------------------------------
# MUSIC_FIRST_REEL stays reserved/unimplemented -- CREATIVE_PLAN generation
# itself doesn't gate on video_style (never has), so it must keep working
# fine for this style; the NotImplementedError still lives where it always
# has, at audio_plan_for() (exercised downstream, not by this provider).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_provider_still_generates_for_music_first_reel():
    from campaign_worker.video.audio_plan import audio_plan_for

    request = _request(video_style=VideoStyle.MUSIC_FIRST_REEL)
    plan = await DeterministicCreativePlanProvider().generate(request)
    assert len(plan.shots) == 6
    with pytest.raises(NotImplementedError):
        audio_plan_for(VideoStyle.MUSIC_FIRST_REEL)


# ---------------------------------------------------------------------------
# Downstream compatibility: image creative intent + HyperFrames adapter both
# consume an AI-generated CreativeVideoPlan exactly as they consume a
# deterministic one -- neither module changes for this slice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_generated_plan_is_consumed_by_image_creative_intent_resolution():
    from campaign_worker.images.creative_intent import resolve_image_creative_intent

    client = _FakeBedrockClient(json.dumps(_valid_plan_dict(shot_count=6)))
    plan = await BedrockCreativePlanProvider(client, "test-model").generate(_request())

    intents = resolve_image_creative_intent(plan)
    assert set(intents.keys()) <= {1, 2, 3}
    assert all(intent.primary_asset_role is not None for intent in intents.values())


@pytest.mark.asyncio
async def test_ai_generated_plan_is_accepted_by_hyperframes_creative_plan_adapter():
    from campaign_worker.video.creative_plan_adapter import build_resolved_shots

    client = _FakeBedrockClient(json.dumps(_valid_plan_dict(shot_count=7)))
    plan = await BedrockCreativePlanProvider(client, "test-model").generate(_request())

    resolved_shots = build_resolved_shots(plan)
    assert len(resolved_shots) == 7
    assert [s.shot_number for s in resolved_shots] == list(range(1, 8))
