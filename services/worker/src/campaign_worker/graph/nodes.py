import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.campaign import (
    CampaignCopy,
    ChannelCopy,
    ImagePrompt,
    ReviewPackage,
    Storyboard,
    StoryboardScene,
    StrategyOutput,
)
from campaign_contracts.enums import CampaignStatus, ErrorComponent, WorkflowStep
from campaign_contracts.errors import SanitizedWorkflowError

from campaign_worker.audio.pipeline import VoiceAssetPipeline
from campaign_worker.errors import WorkflowOperationError
from campaign_worker.images.pipeline import ImageAssetPipeline
from campaign_worker.providers.base import ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.providers.models import ImageGenerationRequest, VideoRenderRequest
from campaign_worker.providers.voice_models import VoiceGenerationRequest
from campaign_worker.video.pipeline import VideoAssetPipeline

from .boundary import NodeCancelled, NodeFn
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


async def create_storyboard(state: GraphState) -> GraphState:
    version = state["version"]
    strategy = version.strategy
    if strategy is None:
        raise ValueError("create_storyboard requires create_strategy to have run first")
    scenes = [
        StoryboardScene(
            scene_number=index,
            purpose=f"Scene {index} for {strategy.objective}",
            duration_seconds=5,
            narration=strategy.key_message,
            visual_prompt=f"{version.brief.product_or_service}, scene {index}",
            transition="cut",
        )
        for index in (1, 2, 3)
    ]
    storyboard = Storyboard(scenes=scenes, total_duration_seconds=15)
    return {"version": version.model_copy(update={"storyboard": storyboard})}


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


def make_generate_voiceover_node(provider: VoiceProvider) -> NodeFn:
    async def generate_voiceover(state: GraphState) -> GraphState:
        version = state["version"]
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
    if version.voice_artifact is None:
        missing.append("voice_artifact")
    if version.video_artifact is None:
        missing.append("video_artifact")
    validation = ReviewPackageValidationResult(is_valid=not missing, missing_artifacts=missing)
    return {**state, "review_validation": validation}


async def await_human_approval(state: GraphState) -> GraphState:
    validation = state.get("review_validation")
    if validation is None:
        raise ValueError("await_human_approval requires validate_review_package to have run first")
    if not validation.is_valid:
        raise ValueError(f"cannot await human approval: review package incomplete: {validation.missing_artifacts}")
    version = state["version"]
    return {**state, "version": version.model_copy(update={"status": CampaignStatus.READY_FOR_REVIEW})}


async def prepare_final_package(state: GraphState) -> GraphState:
    version = state["version"]
    if version.strategy is None or version.campaign_copy is None or version.storyboard is None:
        raise ValueError("prepare_final_package requires strategy, campaign_copy, and storyboard to be present")
    if not version.image_artifacts:
        raise ValueError("prepare_final_package requires generate_images to have run first")
    if version.video_artifact is None:
        raise ValueError("prepare_final_package requires render_video to have run first")

    artifact_ids = [artifact.artifact_id for artifact in version.image_artifacts]
    artifact_ids.append(version.video_artifact.artifact_id)
    signature = f"{version.campaign_id}:{version.campaign_version}:" + ":".join(
        sorted(str(artifact_id) for artifact_id in artifact_ids)
    )
    manifest_checksum = hashlib.sha256(signature.encode()).hexdigest()
    review_package = ReviewPackage(artifact_id=uuid4(), manifest_checksum=manifest_checksum, artifact_ids=artifact_ids)

    updated_version = version.model_copy(update={"review_package": review_package, "status": CampaignStatus.FINAL})
    return {**state, "version": updated_version}


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
