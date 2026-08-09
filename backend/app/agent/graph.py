"""The LangGraph state machine.

The conditional edges are what make this agentic rather than a fixed chain:
the graph decides whether to search at all, whether what it found is good
enough, whether to broaden and try again, and whether the draft it wrote is
actually supported by the sources.

    guard ─> classify ─┬─> chitchat ─────────────────────> END
                       ├─> list_catalog ─────────────────> END
                       ├─> out_of_scope ─────────────────> END
                       └─> rewrite -> retrieve -> grade ─┬─> generate -> verify -> END
                                ^                        ├─> (broaden, max 2) ─┐
                                └────────────────────────┘                     │
                                                         └─> no_answer ──────> END
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.state import AgentState
from app.core.config import settings


def _after_route(state: AgentState) -> str:
    route = state.get("route", "document_qa")
    if route in {"chitchat", "catalog", "out_of_scope"}:
        return route
    return "rewrite"


def _after_grade(state: AgentState) -> str:
    if state.get("sufficient", True):
        return "generate"
    # Every loop in an agent needs an explicit cap. Without one, an unusual
    # question becomes an outage.
    if state.get("attempts", 0) <= settings.max_refine_loops:
        return "rewrite"
    return "no_answer"


def build_graph() -> Any:
    graph = StateGraph(AgentState)

    # Node names must not collide with state keys ("route", "catalog" are both),
    # so the two nodes that would clash are named for the action they perform.
    graph.add_node("guard", nodes.guard)
    graph.add_node("classify", nodes.route)
    graph.add_node("rewrite", nodes.rewrite)
    graph.add_node("retrieve", nodes.retrieve_node)
    graph.add_node("grade", nodes.grade)
    graph.add_node("generate", nodes.generate)
    graph.add_node("verify", nodes.verify)
    graph.add_node("chitchat", nodes.chitchat)
    graph.add_node("list_catalog", nodes.catalog)
    graph.add_node("out_of_scope", nodes.out_of_scope)
    graph.add_node("no_answer", nodes.no_answer)

    graph.add_edge(START, "guard")
    graph.add_edge("guard", "classify")
    graph.add_conditional_edges(
        "classify",
        _after_route,
        {
            "chitchat": "chitchat",
            "catalog": "list_catalog",
            "out_of_scope": "out_of_scope",
            "rewrite": "rewrite",
        },
    )
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        _after_grade,
        {"generate": "generate", "rewrite": "rewrite", "no_answer": "no_answer"},
    )
    graph.add_edge("generate", "verify")
    graph.add_edge("verify", END)
    graph.add_edge("chitchat", END)
    graph.add_edge("list_catalog", END)
    graph.add_edge("out_of_scope", END)
    graph.add_edge("no_answer", END)

    return graph.compile()


@lru_cache(maxsize=1)
def compiled_graph() -> Any:
    """Compiled once per process. The per-request database connection is passed
    through the run config, not baked into the graph."""
    return build_graph()
