"""The graph's state object.

Nodes take state and return a partial update. They never mutate it in place and
never reach outside it for context — that is what makes the graph replayable
and the decision path readable in the logs.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from app.retrieval.hybrid import RetrievedChunk


def _last(_current: Any, incoming: Any) -> Any:
    return incoming


class AgentState(TypedDict, total=False):
    # inputs
    question: Annotated[str, _last]
    department_id: Annotated[str, _last]
    department_name: Annotated[str, _last]
    user_id: Annotated[str, _last]
    history: Annotated[list[dict[str, str]], _last]
    catalog: Annotated[list[dict[str, Any]], _last]

    # working
    route: Annotated[str, _last]
    search_query: Annotated[str, _last]
    chunks: Annotated[list[RetrievedChunk], _last]
    attempts: Annotated[int, _last]
    sufficient: Annotated[bool, _last]
    grade_reason: Annotated[str, _last]

    # outputs
    answer: Annotated[str, _last]
    citations: Annotated[list[dict[str, Any]], _last]
    usage: Annotated[dict[str, int], _last]
    trace: Annotated[list[str], _last]
