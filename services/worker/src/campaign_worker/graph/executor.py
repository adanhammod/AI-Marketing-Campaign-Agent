from collections.abc import Awaitable, Callable
from typing import cast

from campaign_contracts.campaign import CampaignVersion
from campaign_contracts.enums import WorkflowStep
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from campaign_worker.repositories.workflow_repository import WorkflowRepository

from . import nodes as _nodes
from .boundary import with_step_tracking
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


def build_default_graph(repository: WorkflowRepository) -> _CompiledGraph:
    return build_graph(
        nodes={
            "receive_request": _nodes.receive_request,
            "validate_input": _nodes.validate_input,
            "analyze_campaign": _nodes.analyze_campaign,
            "create_strategy": with_step_tracking(WorkflowStep.STRATEGY, repository)(_nodes.create_strategy),
            "generate_copy": with_step_tracking(WorkflowStep.COPY, repository)(_nodes.generate_copy),
            "create_storyboard": with_step_tracking(WorkflowStep.STORYBOARD, repository)(_nodes.create_storyboard),
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


class GraphExecutor:
    def __init__(self, compiled_graph: _CompiledGraph) -> None:
        self._graph = compiled_graph

    async def run(self, version: CampaignVersion) -> CampaignVersion:
        result = await self._graph.ainvoke({"version": version})
        return cast(CampaignVersion, result["version"])
