import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from campaign_contracts.campaign import (
    CampaignConstraints,
    CampaignCopy,
    CampaignVersion,
    ChannelCopy,
    ImagePrompt,
    NormalizedCampaignBrief,
    ReviewPackage,
    Storyboard,
    StoryboardScene,
    StrategyOutput,
)
from campaign_contracts.enums import CampaignStatus, ErrorComponent, WorkflowStep
from campaign_contracts.errors import SanitizedWorkflowError

from campaign_worker.audio import narration_timing
from campaign_worker.audio.pipeline import VoiceAssetPipeline
from campaign_worker.errors import WorkflowOperationError
from campaign_worker.images.pipeline import ImageAssetPipeline
from campaign_worker.package.pipeline import PackageAssetPipeline
from campaign_worker.providers.base import CreativePlanProvider, ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.providers.models import ImageGenerationRequest, VideoRenderRequest
from campaign_worker.providers.voice_models import VoiceGenerationRequest
from campaign_worker.video.audio_plan import audio_plan_for
from campaign_worker.video.pipeline import VideoAssetPipeline

from .boundary import NodeCancelled, NodeFn
from .creative_plan_provider import build_creative_plan_request
from .state import GraphState, ReviewPackageValidationResult


async def receive_request(state: GraphState) -> GraphState:
    return state


async def validate_input(state: GraphState) -> GraphState:
    if not state["version"].brief.business_description.strip():
        raise ValueError("business_description must not be blank")
    return state


async def analyze_campaign(state: GraphState) -> GraphState:
    return state


async def create_strategy(state: GraphState) -> GraphState:
    version = state["version"]
    brief = version.brief
    audience = brief.target_audience or "general audience"
    strategy = StrategyOutput(
        audience=audience,
        positioning=f"{brief.business_name} for {brief.product_or_service}",
        objective=brief.campaign_goal,
        key_message=brief.key_message or brief.campaign_goal,
        channel_rationale={platform: f"Reach {audience} on {platform}" for platform in brief.platforms},
    )
    return {"version": version.model_copy(update={"strategy": strategy})}


async def generate_copy(state: GraphState) -> GraphState:
    version = state["version"]
    brief = version.brief
    strategy = version.strategy
    if strategy is None:
        raise ValueError("generate_copy requires create_strategy to have run first")
    headline = f"{brief.business_name}: {strategy.key_message}"[:120]
    caption = brief.business_description[:200]
    cta = brief.call_to_action or "Learn more"
    hashtags = [f"#{brief.business_name.replace(' ', '')}"]
    copy = CampaignCopy(
        headline=headline,
        caption=caption,
        call_to_action=cta,
        hashtags=hashtags,
        channel_variants=[
            ChannelCopy(channel=platform, headline=headline, caption=caption, call_to_action=cta, hashtags=hashtags)
            for platform in brief.platforms
        ],
    )
    return {"version": version.model_copy(update={"campaign_copy": copy})}


def _first_complete_sentence(text: str) -> str:
    # The whole first sentence, up to and including its own terminal '.',
    # '!', or '?'. Never slices mid-word/mid-clause: if a field contains
    # multiple sentences, later ones are simply dropped (a complete
    # standalone sentence, not a fragment); if it contains no terminal
    # punctuation at all, the entire text is kept as-is (plus a trailing
    # period), never cut short.
    text = text.strip()
    for index, char in enumerate(text):
        if char in ".!?":
            return text[: index + 1]
    return f"{text}." if text else text


@dataclass(frozen=True, slots=True)
class SceneNarration:
    """A scene's narration split into required (never droppable) and
    optional (safe-to-omit filler) content, so duration budgeting can
    shrink narration by removing only `optional` -- required always survives
    intact, unmodified, un-truncated."""

    required: str
    optional: str | None = None

    def full_text(self) -> str:
        return self.required if self.optional is None else f"{self.required} {self.optional}"


def _scene_narration(scene_number: int, brief: NormalizedCampaignBrief, strategy: StrategyOutput) -> SceneNarration:
    # Deterministic, distinct-per-scene narration built from existing brief/
    # strategy fields (no LLM call). Scene 2 deliberately does not narrate
    # strategy.audience: it's a targeting spec (e.g. "aged 22-40"), not ad
    # copy -- no real 15-second ad reads a demographic aloud, and narrating
    # it verbatim caused the real Luna production incident's flat,
    # read-aloud delivery. product_or_service/business_name are names and
    # are never modified here -- cutting a name mid-word/mid-phrase is never
    # acceptable, even under duration pressure (see _apply_duration_budget,
    # which only ever *extends* or drops -- via the `optional` field above --
    # never trims/truncates anything). key_message/CTA can be free-form
    # prose, so only their first complete sentence is used -- dropping any
    # trailing sentences is safe (what remains still reads as a complete,
    # standalone thought), unlike cutting mid-sentence. The trailing
    # "You'll love it, too."/"Explore ... today." clauses are marked
    # `optional`: safe filler/reinforcement, droppable without losing
    # essential campaign meaning (product/business name is already stated
    # in scene 1; the CTA/key_message sentence itself is `required`).
    product = brief.product_or_service
    business_name = brief.business_name
    if scene_number == 1:
        return SceneNarration(required=f"Meet {product}, made by {business_name}.")
    if scene_number == 2:
        key_message = _first_complete_sentence(strategy.key_message)
        return SceneNarration(required=key_message, optional="You'll love it, too.")
    cta = _first_complete_sentence(brief.call_to_action or "Learn more")
    return SceneNarration(required=cta, optional=f"Explore {product} from {business_name} today.")


def _extension_candidates(brief: NormalizedCampaignBrief) -> list[str]:
    # Real, campaign-specific complete sentences available to extend short
    # narration toward the target word band -- not repeated/arbitrary
    # filler, and never a truncated fragment of one. business_description
    # and tone are both required brief fields (always present, unlike
    # optional target_audience/key_message/CTA), and neither is used
    # anywhere else in narration, so this is genuinely new content.
    # target_audience/strategy.audience is deliberately never used here:
    # narrating a raw targeting spec caused the real Luna production
    # incident (see _scene_narration above and its regression tests).
    # Each candidate here is a *naturally* differently-sized complete
    # sentence (not manufactured short/medium/long variants of the same
    # content) -- the fill algorithm below picks whichever complete
    # candidate best fits the remaining budget.
    description = _first_complete_sentence(brief.business_description)
    tone_sentence = f"It's {brief.tone.strip()}."
    return [description, tone_sentence]


def _best_fitting_candidate(candidates: list[str], remaining_words: int, used: set[str]) -> str | None:
    """The longest not-yet-used complete candidate that fits within
    remaining_words -- never a truncated one. Returns None (add nothing)
    if not even the shortest available complete candidate fits; forcing a
    too-long candidate in by cutting it is never an acceptable fallback."""
    fitting = [c for c in candidates if c and c not in used and narration_timing.word_count(c) <= remaining_words]
    if not fitting:
        return None
    return max(fitting, key=narration_timing.word_count)


def _apply_duration_budget(
    scene_narrations: list[SceneNarration], brief: NormalizedCampaignBrief, constraints: CampaignConstraints
) -> list[str]:
    """Couples narration length to the campaign's duration constraint before
    Polly is ever called -- symmetrically, but not aggressively:

    - Too short (estimate below the preferred TARGET band): *extend* with
      real, complete, appropriately-sized content (never by
      trimming/cutting anything -- see _best_fitting_candidate).
    - Too long (estimate above shrink_trigger_word_count's hard-max-relative
      threshold): drop only `optional` filler (see SceneNarration), never
      `required` content -- business_name/product_or_service/the
      required key_message or CTA sentence always survive intact. Narration
      merely above the ideal TARGET band but still comfortably under the
      hard max is deliberately left untouched: the real Polly-measured
      duration (audio/pipeline.py) plus a bounded tempo correction
      (audio/tempo_normalizer.py) are what actually decide such cases now,
      not this best-effort generation-time estimate.

    If dropping every scene's optional filler still leaves the estimate
    over budget (e.g. an inherently long business/product name), it's left
    as-is: mangling required campaign content to force a fit is never an
    acceptable fallback here either.
    """
    min_words, max_words = narration_timing.target_word_range()
    texts = [sn.full_text() for sn in scene_narrations]
    total = sum(narration_timing.word_count(t) for t in texts)

    shrink_trigger = narration_timing.shrink_trigger_word_count(constraints.max_duration_seconds)
    if total > shrink_trigger:
        for index in (2, 1):  # scene 3's filler is the most redundant (restates scene 1); scene 2's next
            if total <= shrink_trigger:
                break
            optional = scene_narrations[index].optional
            if optional is None:
                continue
            texts[index] = scene_narrations[index].required
            total -= narration_timing.word_count(optional)
        return texts

    if total >= min_words:
        return texts

    candidates = _extension_candidates(brief)
    used: set[str] = set()
    for index in (0, 2):  # scenes 1 and 3; scene 2 stays anchored to key_message
        if total >= min_words:
            break
        candidate = _best_fitting_candidate(candidates, max_words - total, used)
        if candidate is None:
            continue
        texts[index] = f"{texts[index]} {candidate}".strip()
        total += narration_timing.word_count(candidate)
        used.add(candidate)

    return texts


async def create_storyboard(state: GraphState) -> GraphState:
    version = state["version"]
    strategy = version.strategy
    if strategy is None:
        raise ValueError("create_storyboard requires create_strategy to have run first")
    narrations = _apply_duration_budget(
        [_scene_narration(index, version.brief, strategy) for index in (1, 2, 3)],
        version.brief,
        version.constraints,
    )
    scenes = [
        StoryboardScene(
            scene_number=index,
            purpose=f"Scene {index} for {strategy.objective}",
            duration_seconds=5,
            narration=narrations[index - 1],
            visual_prompt=f"{version.brief.product_or_service}, scene {index}",
            transition="cut",
        )
        for index in (1, 2, 3)
    ]
    storyboard = Storyboard(scenes=scenes, total_duration_seconds=15)
    return {"version": version.model_copy(update={"storyboard": storyboard})}


def make_create_creative_plan_node(provider: CreativePlanProvider) -> NodeFn:
    async def create_creative_plan(state: GraphState) -> GraphState:
        version = state["version"]
        storyboard = version.storyboard
        if storyboard is None:
            raise ValueError("create_creative_plan requires create_storyboard to have run first")
        strategy = version.strategy
        if strategy is None:
            raise ValueError("create_creative_plan requires create_strategy to have run first")
        campaign_copy = version.campaign_copy
        if campaign_copy is None:
            raise ValueError("create_creative_plan requires generate_copy to have run first")

        request = build_creative_plan_request(version)
        plan = await provider.generate(request)
        return {"version": version.model_copy(update={"creative_video_plan": plan})}

    return create_creative_plan


def make_generate_images_node(provider: ImageProvider) -> NodeFn:
    async def generate_images(state: GraphState) -> GraphState:
        version = state["version"]
        storyboard = version.storyboard
        if storyboard is None:
            raise ValueError("generate_images requires create_storyboard to have run first")
        artifacts = []
        for scene in storyboard.scenes:
            prompt = ImagePrompt(
                scene_number=scene.scene_number,
                prompt=scene.visual_prompt,
                aspect_ratio=version.constraints.aspect_ratio,
            )
            request = ImageGenerationRequest(
                campaign_id=version.campaign_id, campaign_version=version.campaign_version, prompt=prompt
            )
            result = await provider.generate_image(request)
            if result.artifact is None:
                raise ValueError(f"image generation failed for scene {scene.scene_number}: {result.error}")
            artifacts.append(result.artifact)
        return {"version": version.model_copy(update={"image_artifacts": artifacts})}

    return generate_images


def make_acquire_images_node(pipeline: ImageAssetPipeline, is_cancelled: Callable[[], Awaitable[bool]]) -> NodeFn:
    async def acquire_images(state: GraphState) -> GraphState:
        version = state["version"]
        artifacts = await pipeline.acquire(version, is_cancelled)
        return {"version": version.model_copy(update={"image_artifacts": artifacts})}

    return acquire_images


def _skip_voiceover(version: CampaignVersion) -> GraphState:
    return {
        "version": version,
        "_step_skipped": True,
        "_skip_reason": f"video_style={version.brief.video_style.value}",
    }


def make_generate_voiceover_node(provider: VoiceProvider) -> NodeFn:
    async def generate_voiceover(state: GraphState) -> GraphState:
        version = state["version"]
        if not audio_plan_for(version.brief.video_style).voiceover_required:
            return _skip_voiceover(version)
        storyboard = version.storyboard
        if storyboard is None:
            raise ValueError("generate_voiceover requires create_storyboard to have run first")
        narration_text = " ".join(scene.narration for scene in storyboard.scenes)
        request = VoiceGenerationRequest(
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            narration_text=narration_text,
        )
        result = await provider.generate_voice(request)
        if result.artifact is None:
            raise ValueError(f"voice generation failed: {result.error}")
        return {"version": version.model_copy(update={"voice_artifact": result.artifact})}

    return generate_voiceover


def make_acquire_voiceover_node(pipeline: VoiceAssetPipeline, is_cancelled: Callable[[], Awaitable[bool]]) -> NodeFn:
    async def acquire_voiceover(state: GraphState) -> GraphState:
        version = state["version"]
        if not audio_plan_for(version.brief.video_style).voiceover_required:
            return _skip_voiceover(version)
        artifact = await pipeline.acquire(version, is_cancelled)
        return {"version": version.model_copy(update={"voice_artifact": artifact})}

    return acquire_voiceover


def make_render_video_node(provider: VideoProvider) -> NodeFn:
    async def render_video(state: GraphState) -> GraphState:
        version = state["version"]
        storyboard = version.storyboard
        if storyboard is None:
            raise ValueError("render_video requires create_storyboard to have run first")
        if not version.image_artifacts:
            raise ValueError("render_video requires generate_images to have run first")
        voice_artifact = version.voice_artifact
        if voice_artifact is None:
            raise ValueError("render_video requires generate_voiceover to have run first")
        request = VideoRenderRequest(
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            storyboard=storyboard,
            image_artifacts=version.image_artifacts,
            voice_artifact=voice_artifact,
            aspect_ratio=version.constraints.aspect_ratio,
        )
        result = await provider.render_video(request)
        if result.artifact is None:
            raise ValueError(f"video rendering failed: {result.error}")
        return {"version": version.model_copy(update={"video_artifact": result.artifact})}

    return render_video


def make_acquire_video_node(pipeline: VideoAssetPipeline, is_cancelled: Callable[[], Awaitable[bool]]) -> NodeFn:
    async def acquire_video(state: GraphState) -> GraphState:
        version = state["version"]
        artifact = await pipeline.acquire(version, is_cancelled)
        return {"version": version.model_copy(update={"video_artifact": artifact})}

    return acquire_video


async def validate_review_package(state: GraphState) -> GraphState:
    version = state["version"]
    missing: list[str] = []
    if version.strategy is None:
        missing.append("strategy")
    if version.campaign_copy is None:
        missing.append("campaign_copy")
    if version.storyboard is None:
        missing.append("storyboard")
    if not version.image_artifacts:
        missing.append("image_artifacts")
    if audio_plan_for(version.brief.video_style).voiceover_required and version.voice_artifact is None:
        missing.append("voice_artifact")
    if version.video_artifact is None:
        missing.append("video_artifact")
    validation = ReviewPackageValidationResult(is_valid=not missing, missing_artifacts=missing)
    return {**state, "review_validation": validation}


def deterministic_review_package_artifact_id(campaign_id: UUID, campaign_version: int) -> UUID:
    return uuid5(campaign_id, f"version:{campaign_version}:review_package")


def _build_review_package(version: CampaignVersion) -> ReviewPackage:
    # Deterministic given the same persisted artifact IDs: safe to recompute on SQS
    # redelivery of an already-READY_FOR_REVIEW version without violating immutability.
    artifact_ids = [artifact.artifact_id for artifact in version.image_artifacts]
    if version.video_artifact is not None:
        artifact_ids.append(version.video_artifact.artifact_id)
    signature = f"{version.campaign_id}:{version.campaign_version}:" + ":".join(
        sorted(str(artifact_id) for artifact_id in artifact_ids)
    )
    manifest_checksum = hashlib.sha256(signature.encode()).hexdigest()
    return ReviewPackage(
        artifact_id=deterministic_review_package_artifact_id(version.campaign_id, version.campaign_version),
        manifest_checksum=manifest_checksum,
        artifact_ids=artifact_ids,
    )


async def await_human_approval(state: GraphState) -> GraphState:
    validation = state.get("review_validation")
    if validation is None:
        raise ValueError("await_human_approval requires validate_review_package to have run first")
    if not validation.is_valid:
        raise ValueError(f"cannot await human approval: review package incomplete: {validation.missing_artifacts}")
    version = state["version"]
    review_package = _build_review_package(version)
    updated_version = version.model_copy(
        update={"status": CampaignStatus.READY_FOR_REVIEW, "review_package": review_package}
    )
    return {**state, "version": updated_version}


def make_prepare_final_package_node(
    package_pipeline: PackageAssetPipeline, is_cancelled: Callable[[], Awaitable[bool]]
) -> NodeFn:
    async def prepare_final_package(state: GraphState) -> GraphState:
        version = state["version"]
        if version.strategy is None or version.campaign_copy is None or version.storyboard is None:
            raise ValueError("prepare_final_package requires strategy, campaign_copy, and storyboard to be present")
        if not version.image_artifacts:
            raise ValueError("prepare_final_package requires generate_images to have run first")
        if version.video_artifact is None:
            raise ValueError("prepare_final_package requires render_video to have run first")
        if version.review_package is None:
            raise ValueError("prepare_final_package requires a review package established prior to approval")

        package_artifact = await package_pipeline.acquire(version, is_cancelled)
        updated_version = version.model_copy(
            update={"package_artifact": package_artifact, "status": CampaignStatus.FINAL}
        )
        return {**state, "version": updated_version}

    return prepare_final_package


async def handle_failure(state: GraphState, error: BaseException, *, step: WorkflowStep | None = None) -> GraphState:
    version = state["version"]
    now = datetime.now(UTC)

    if isinstance(error, NodeCancelled):
        sanitized_error = SanitizedWorkflowError(
            code="CANCELLED_BY_USER",
            message=str(error)[:500],
            component=ErrorComponent.LANGGRAPH_WORKER,
            workflow_step=step,
            attempt=max(version.retry.attempt, 1),
            retryable=False,
            timestamp=now,
            correlation_id=uuid4(),
            campaign_id=version.campaign_id,
            campaign_version=version.campaign_version,
            job_id=version.job_id,
        )
        updated_version = version.model_copy(
            update={
                "status": CampaignStatus.CANCELLED,
                "error": sanitized_error,
                "retry": version.retry.model_copy(update={"retryable": False}),
            }
        )
        return {**state, "version": updated_version}

    next_attempt = version.retry.attempt + 1
    operation_error = error if isinstance(error, WorkflowOperationError) else None
    requested_retry = operation_error.retryable if operation_error is not None else True
    retryable = requested_retry and next_attempt < version.retry.max_attempts
    sanitized_error = SanitizedWorkflowError(
        code=(
            operation_error.code
            if operation_error is not None
            else ("RETRY_EXHAUSTED" if not retryable else "INTERNAL_ERROR")
        ),
        message=str(error)[:500],
        component=ErrorComponent.LANGGRAPH_WORKER,
        workflow_step=step,
        attempt=next_attempt,
        retryable=retryable,
        timestamp=now,
        correlation_id=uuid4(),
        campaign_id=version.campaign_id,
        campaign_version=version.campaign_version,
        job_id=version.job_id,
    )
    updated_version = version.model_copy(
        update={
            "status": CampaignStatus.FAILED,
            "error": sanitized_error,
            "retry": version.retry.model_copy(
                update={"attempt": next_attempt, "retryable": retryable, "resume_step": step}
            ),
        }
    )
    return {**state, "version": updated_version}
