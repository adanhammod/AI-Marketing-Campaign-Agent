from collections.abc import Awaitable, Callable
from typing import cast

from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from campaign_worker.providers.base import ImageProvider, VideoProvider, VoiceProvider
from campaign_worker.repositories.workflow_repository import WorkflowRepository

from . import nodes as _nodes
from .boundary import with_cancellation_check, with_failure_attribution, with_step_tracking
from .state import GraphState

NodeFn = Callable[[GraphState], Awaitable[GraphState]]
_CompiledGraph = CompiledStateGraph[GraphState, None, GraphState, GraphState]


def build_graph(nodes: dict[str, NodeFn], edges: list[tuple[str, ...]]) -> _CompiledGraph:
    graph = StateGraph(GraphState)
    for name, fn in nodes.items():
        # langgraph's node overloads don't structurally match a plain async Callable,
        # though it accepts one at runtime.
        graph.add_node(name, fn)  # type: ignore[call-overload]
    previous = START
    for (name,) in edges:
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


def build_default_graph(repository: WorkflowRepository, is_cancelled: Callable[[], Awaitable[bool]]) -> _CompiledGraph:
    def cancellable(name: str, fn: NodeFn) -> NodeFn:
        return with_cancellation_check(is_cancelled, name)(fn)

    return build_graph(
        nodes={
            "receive_request": cancellable("receive_request", _nodes.receive_request),
            "validate_input": cancellable("validate_input", _nodes.validate_input),
            "analyze_campaign": cancellable("analyze_campaign", _nodes.analyze_campaign),
            "create_strategy": cancellable(
                "create_strategy", with_step_tracking(WorkflowStep.STRATEGY, repository)(_nodes.create_strategy)
            ),
            "generate_copy": cancellable(
                "generate_copy", with_step_tracking(WorkflowStep.COPY, repository)(_nodes.generate_copy)
            ),
            "create_storyboard": cancellable(
                "create_storyboard",
                with_step_tracking(WorkflowStep.STORYBOARD, repository)(_nodes.create_storyboard),
            ),
        },
        edges=[
            ("receive_request",),
            ("validate_input",),
            ("analyze_campaign",),
            ("create_strategy",),
            ("generate_copy",),
            ("create_storyboard",),
        ],
    )


def build_start_graph(
    repository: WorkflowRepository,
    is_cancelled: Callable[[], Awaitable[bool]],
    image_provider: ImageProvider,
    voice_provider: VoiceProvider,
    video_provider: VideoProvider,
) -> _CompiledGraph:
    def cancellable(name: str, fn: NodeFn) -> NodeFn:
        return with_cancellation_check(is_cancelled, name)(fn)

    def tracked_step(name: str, step: WorkflowStep, fn: NodeFn) -> NodeFn:
        # with_failure_attribution wraps outermost so it also catches NodeCancelled,
        # tagging any failure at this node -- business logic or cancellation alike --
        # with the WorkflowStep that was executing.
        return with_failure_attribution(step)(cancellable(name, with_step_tracking(step, repository)(fn)))

    return build_graph(
        nodes={
            "receive_request": cancellable("receive_request", _nodes.receive_request),
            "validate_input": cancellable("validate_input", _nodes.validate_input),
            "analyze_campaign": cancellable("analyze_campaign", _nodes.analyze_campaign),
            "create_strategy": tracked_step("create_strategy", WorkflowStep.STRATEGY, _nodes.create_strategy),
            "generate_copy": tracked_step("generate_copy", WorkflowStep.COPY, _nodes.generate_copy),
            "create_storyboard": tracked_step("create_storyboard", WorkflowStep.STORYBOARD, _nodes.create_storyboard),
            "generate_images": tracked_step(
                "generate_images", WorkflowStep.IMAGES, _nodes.make_generate_images_node(image_provider)
            ),
            "generate_voiceover": cancellable(
                "generate_voiceover", _nodes.make_generate_voiceover_node(voice_provider)
            ),
            "render_video": tracked_step(
                "render_video", WorkflowStep.VIDEO, _nodes.make_render_video_node(video_provider)
            ),
            "validate_review_package": cancellable("validate_review_package", _nodes.validate_review_package),
            "await_human_approval": cancellable("await_human_approval", _nodes.await_human_approval),
        },
        edges=[
            ("receive_request",),
            ("validate_input",),
            ("analyze_campaign",),
            ("create_strategy",),
            ("generate_copy",),
            ("create_storyboard",),
            ("generate_images",),
            ("generate_voiceover",),
            ("render_video",),
            ("validate_review_package",),
            ("await_human_approval",),
        ],
    )


def build_resume_graph(is_cancelled: Callable[[], Awaitable[bool]]) -> _CompiledGraph:
    def cancellable(name: str, fn: NodeFn) -> NodeFn:
        return with_cancellation_check(is_cancelled, name)(fn)

    return build_graph(
        nodes={"prepare_final_package": cancellable("prepare_final_package", _nodes.prepare_final_package)},
        edges=[("prepare_final_package",)],
    )


class GraphExecutor:
    def __init__(self, compiled_graph: _CompiledGraph) -> None:
        self._graph = compiled_graph

    async def run(self, version: CampaignVersion) -> CampaignVersion:
        result = await self._graph.ainvoke({"version": version})
        return cast(CampaignVersion, result["version"])
