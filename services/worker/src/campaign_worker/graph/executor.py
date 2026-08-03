from collections.abc import Awaitable, Callable
from typing import cast

from campaign_contracts.campaign import CampaignVersion
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

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


class GraphExecutor:
    def __init__(self, compiled_graph: _CompiledGraph) -> None:
        self._graph = compiled_graph

    async def run(self, version: CampaignVersion) -> CampaignVersion:
        result = await self._graph.ainvoke({"version": version})
        return cast(CampaignVersion, result["version"])
