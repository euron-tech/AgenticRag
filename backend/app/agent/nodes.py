"""Graph nodes. One node, one responsibility.

Routing does not retrieve. Generation does not grade. Every node logs the
decision it made with the request id, so when an answer is wrong the logs show
which path the graph took.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent import prompts
from app.agent.llm import complete_json, complete_text, merge_usage
from app.agent.state import AgentState
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import emit
from app.retrieval.hybrid import build_context

log = get_logger(__name__)

VALID_ROUTES = {"chitchat", "document_qa", "catalog", "summarize", "out_of_scope"}

# Patterns that only ever appear in an attempt to talk to the model rather than
# to the documents. Screened, logged, and then answered normally.
_INJECTION = re.compile(
    r"(ignore (all |your )?(previous|prior|above) instructions"
    r"|disregard (the )?(system|previous) prompt"
    r"|reveal (your )?(system prompt|instructions|api key)"
    r"|you are now (a|an) )",
    re.IGNORECASE,
)


def _trace(state: AgentState, label: str) -> list[str]:
    return [*state.get("trace", []), label]


def _history_messages(state: AgentState) -> list[dict[str, str]]:
    turns = state.get("history", [])[-settings.history_turns :]
    return [{"role": t["role"], "content": t["content"]} for t in turns]


# ------------------------------------------------------------------- guard
async def guard(state: AgentState) -> dict[str, Any]:
    question = state.get("question", "")
    if _INJECTION.search(question):
        log.warning("prompt_injection_pattern", extra={"length": len(question)})
        emit({"InjectionAttempts": 1.0}, unit="Count")
    return {"attempts": 0, "trace": _trace(state, "guard")}


# ------------------------------------------------------------------- route
async def route(state: AgentState) -> dict[str, Any]:
    payload, response = await complete_json(
        prompts.ROUTE_SYSTEM,
        [
            *_history_messages(state),
            {"role": "user", "content": state.get("question", "")},
        ],
        default={"route": "document_qa", "reason": "default"},
    )
    chosen = str(payload.get("route", "document_qa"))
    if chosen not in VALID_ROUTES:
        chosen = "document_qa"

    log.info("agent_route", extra={"route": chosen, "reason": payload.get("reason")})
    emit({"AgentRoute": 1.0}, unit="Count", dimensions={"Route": chosen})
    return {
        "route": chosen,
        "usage": merge_usage(state.get("usage"), response),
        "trace": _trace(state, f"route:{chosen}"),
    }


# ----------------------------------------------------------------- rewrite
async def rewrite(state: AgentState) -> dict[str, Any]:
    question = state.get("question", "")
    attempts = state.get("attempts", 0)

    if attempts == 0 and not state.get("history"):
        # Nothing to resolve against; an LLM call here would only add latency.
        return {"search_query": question, "trace": _trace(state, "rewrite:passthrough")}

    system = prompts.BROADEN_SYSTEM if attempts > 0 else prompts.REWRITE_SYSTEM
    context = (
        state.get("search_query", question) if attempts > 0 else question
    )
    payload, response = await complete_json(
        system,
        [*_history_messages(state), {"role": "user", "content": context}],
        default={"query": question},
    )
    query = str(payload.get("query") or question).strip() or question
    log.info("agent_rewrite", extra={"attempt": attempts})
    return {
        "search_query": query,
        "usage": merge_usage(state.get("usage"), response),
        "trace": _trace(state, f"rewrite:{attempts}"),
    }


# ---------------------------------------------------------------- retrieve
async def retrieve_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    # The caller supplies a retriever that opens its own short-lived RLS
    # connection. Holding one across the graph would pin a pool slot for the
    # whole turn, and the LLM calls are the slow part.
    retriever = config.get("configurable", {})["retriever"]
    chunks = await retriever(state.get("search_query") or state["question"])
    log.info("agent_retrieve", extra={"results": len(chunks)})
    return {"chunks": chunks, "trace": _trace(state, f"retrieve:{len(chunks)}")}


# ------------------------------------------------------------------- grade
async def grade(state: AgentState) -> dict[str, Any]:
    chunks = state.get("chunks", [])
    if not chunks:
        return {
            "sufficient": False,
            "grade_reason": "no sources retrieved",
            "attempts": state.get("attempts", 0) + 1,
            "trace": _trace(state, "grade:empty"),
        }

    payload, response = await complete_json(
        prompts.GRADE_SYSTEM,
        [
            {
                "role": "user",
                "content": (
                    f"Question: {state['question']}\n\n"
                    f"Sources:\n{build_context(chunks)}"
                ),
            }
        ],
        default={"sufficient": True, "relevant_ids": [], "reason": "grader unavailable"},
    )

    sufficient = bool(payload.get("sufficient", True))
    relevant = payload.get("relevant_ids") or []
    kept = chunks
    if sufficient and isinstance(relevant, list) and relevant:
        wanted = {int(i) for i in relevant if str(i).isdigit()}
        subset = [c for i, c in enumerate(chunks, start=1) if i in wanted]
        if subset:
            kept = subset

    log.info(
        "agent_grade",
        extra={"sufficient": sufficient, "kept": len(kept),
               "reason": payload.get("reason")},
    )
    return {
        "chunks": kept,
        "sufficient": sufficient,
        "grade_reason": str(payload.get("reason", "")),
        "attempts": state.get("attempts", 0) + (0 if sufficient else 1),
        "usage": merge_usage(state.get("usage"), response),
        "trace": _trace(state, f"grade:{'ok' if sufficient else 'weak'}"),
    }


# ---------------------------------------------------------------- generate
_CITE = re.compile(r"\[(\d+)\]")


async def generate(state: AgentState) -> dict[str, Any]:
    chunks = state.get("chunks", [])
    system = prompts.ANSWER_SYSTEM.format(injection_guard=prompts.INJECTION_GUARD)
    answer, response = await complete_text(
        system,
        [
            *_history_messages(state),
            {
                "role": "user",
                "content": (
                    f"Question: {state['question']}\n\n"
                    f"Sources:\n{build_context(chunks)}"
                ),
            },
        ],
    )

    citations = _citations_for(answer, chunks)
    emit(
        {"CitationsPerAnswer": float(len(citations))},
        unit="Count",
        dimensions={"Route": state.get("route", "document_qa")},
    )
    return {
        "answer": answer,
        "citations": citations,
        "usage": merge_usage(state.get("usage"), response),
        "trace": _trace(state, "generate"),
    }


def _citations_for(answer: str, chunks: list[Any]) -> list[dict[str, Any]]:
    """Only markers the model actually used, mapped back to real chunk ids.

    Citations are assembled from the retrieval result — never from anything the
    model asserts about its sources.
    """
    used = sorted({int(m) for m in _CITE.findall(answer)})
    out: list[dict[str, Any]] = []
    for marker in used:
        if 1 <= marker <= len(chunks):
            out.append(chunks[marker - 1].as_citation(marker))
    return out


# ------------------------------------------------------------------ verify
async def verify(state: AgentState) -> dict[str, Any]:
    answer = state.get("answer", "")
    chunks = state.get("chunks", [])
    if not answer or not chunks:
        return {"trace": _trace(state, "verify:skipped")}

    payload, response = await complete_json(
        prompts.VERIFY_SYSTEM,
        [
            {
                "role": "user",
                "content": f"Draft answer:\n{answer}\n\nSources:\n{build_context(chunks)}",
            }
        ],
        default={"grounded": True, "problems": []},
    )
    grounded = bool(payload.get("grounded", True))
    usage = merge_usage(state.get("usage"), response)

    if grounded:
        return {"usage": usage, "trace": _trace(state, "verify:ok")}

    problems = payload.get("problems") or []
    log.warning("agent_ungrounded", extra={"problems": problems})
    emit({"UngroundedDrafts": 1.0}, unit="Count")

    system = prompts.ANSWER_SYSTEM.format(injection_guard=prompts.INJECTION_GUARD)
    corrected, retry = await complete_text(
        system,
        [
            {
                "role": "user",
                "content": (
                    f"Question: {state['question']}\n\n"
                    f"Sources:\n{build_context(chunks)}\n\n"
                    f"A previous draft was rejected for: {problems}. "
                    "Write an answer containing only what the sources support. "
                    "If they do not support an answer, say so plainly."
                ),
            }
        ],
    )
    return {
        "answer": corrected,
        "citations": _citations_for(corrected, chunks),
        "usage": merge_usage(usage, retry),
        "trace": _trace(state, "verify:regenerated"),
    }


# ------------------------------------------------------- terminal responses
async def chitchat(state: AgentState) -> dict[str, Any]:
    system = prompts.CHITCHAT_SYSTEM.format(
        department=state.get("department_name", "this")
    )
    answer, response = await complete_text(
        system,
        [*_history_messages(state), {"role": "user", "content": state["question"]}],
        temperature=0.4,
        max_tokens=200,
    )
    return {
        "answer": answer,
        "citations": [],
        "usage": merge_usage(state.get("usage"), response),
        "trace": _trace(state, "chitchat"),
    }


async def catalog(state: AgentState) -> dict[str, Any]:
    documents = state.get("catalog", [])
    if not documents:
        return {
            "answer": "No documents have been uploaded to this department yet.",
            "citations": [],
            "trace": _trace(state, "catalog:empty"),
        }
    listing = "\n".join(
        f"- {d['filename']} ({d['status']}"
        + (f", {d['chunk_count']} sections" if d.get("chunk_count") else "")
        + ")"
        for d in documents
    )
    answer, response = await complete_text(
        prompts.CATALOG_SYSTEM,
        [{"role": "user", "content": f"Question: {state['question']}\n\nDocuments:\n{listing}"}],
        max_tokens=600,
    )
    return {
        "answer": answer,
        "citations": [],
        "usage": merge_usage(state.get("usage"), response),
        "trace": _trace(state, "catalog"),
    }


async def out_of_scope(state: AgentState) -> dict[str, Any]:
    return {
        "answer": prompts.OUT_OF_SCOPE_MESSAGE,
        "citations": [],
        "trace": _trace(state, "out_of_scope"),
    }


async def no_answer(state: AgentState) -> dict[str, Any]:
    emit({"NoAnswer": 1.0}, unit="Count")
    log.info("agent_no_answer", extra={"reason": state.get("grade_reason")})
    return {
        "answer": prompts.NO_ANSWER_MESSAGE,
        "citations": [],
        "trace": _trace(state, "no_answer"),
    }
