"""Chat orchestration: conversation state in, agent run, answer persisted."""

from __future__ import annotations

import time
from typing import Any

from app.agent.graph import compiled_graph
from app.core.deps import CurrentUser
from app.core.errors import Forbidden, NotFound
from app.core.logging import get_logger
from app.core.metrics import emit, timed
from app.db import repositories as repo
from app.db.pool import rls_conn
from app.retrieval.hybrid import RetrievedChunk, retrieve

log = get_logger(__name__)

TITLE_MAX = 60


def _title_from(message: str) -> str:
    single_line = " ".join(message.split())
    return single_line[:TITLE_MAX] + ("…" if len(single_line) > TITLE_MAX else "")


def _make_retriever(user_id: str, department_id: str):
    """A retriever that opens and releases its own RLS connection per call."""

    async def _retrieve(query: str) -> list[RetrievedChunk]:
        async with rls_conn(user_id) as conn:
            return await retrieve(conn, department_id=department_id, query=query)

    return _retrieve


async def answer_question(
    *,
    user: CurrentUser,
    department_id: str,
    message: str,
    conversation_id: str | None,
) -> dict[str, Any]:
    started = time.perf_counter()

    async with rls_conn(user.id) as conn:
        department = await repo.get_department(conn, department_id)
        if department is None:
            # Under RLS an inaccessible department is simply invisible.
            raise NotFound("Department not found, or you do not have access to it.")

        if conversation_id:
            conversation = await repo.get_conversation(conn, conversation_id)
            if conversation is None:
                raise NotFound("Conversation not found.")
            if str(conversation["user_id"]) != user.id:
                raise Forbidden("This conversation belongs to another user.")
            if str(conversation["department_id"]) != str(department_id):
                raise Forbidden("This conversation belongs to a different department.")
        else:
            conversation = await repo.create_conversation(
                conn,
                user_id=user.id,
                department_id=department_id,
                title=_title_from(message),
            )
            conversation_id = str(conversation["id"])

        history = await repo.list_messages(conn, conversation_id=conversation_id)
        catalog = await repo.list_documents(conn, department_id)
        await repo.insert_message(
            conn, conversation_id=conversation_id, role="user", content=message
        )

    initial = {
        "question": message,
        "department_id": str(department_id),
        "department_name": department["name"],
        "user_id": user.id,
        "history": [{"role": m["role"], "content": m["content"]} for m in history],
        "catalog": [
            {
                "filename": d["filename"],
                "status": d["status"],
                "chunk_count": d["chunk_count"],
            }
            for d in catalog
        ],
        "usage": {},
        "trace": [],
    }

    with timed("ChatTurnDuration", dimensions={"Department": department["slug"]}) as span:
        final = await compiled_graph().ainvoke(
            initial,
            config={
                "configurable": {"retriever": _make_retriever(user.id, str(department_id))},
                "recursion_limit": 25,
            },
        )
        span["route"] = final.get("route")

    latency_ms = int((time.perf_counter() - started) * 1000)
    answer = final.get("answer") or ""
    citations = final.get("citations") or []
    usage = final.get("usage") or {}

    async with rls_conn(user.id) as conn:
        message_id = await repo.insert_message(
            conn,
            conversation_id=conversation_id,
            role="assistant",
            content=answer,
            citations=citations,
            usage=usage,
            latency_ms=latency_ms,
        )

    if usage.get("total_tokens"):
        emit(
            {"ChatTokens": float(usage["total_tokens"])},
            unit="Count",
            dimensions={"Model": "chat"},
        )

    log.info(
        "chat_turn",
        extra={
            "conversation_id": conversation_id,
            "department_id": str(department_id),
            "route": final.get("route"),
            "citations": len(citations),
            "latency_ms": latency_ms,
            "trace": final.get("trace"),
        },
    )

    return {
        "conversation_id": str(conversation_id),
        "message_id": message_id,
        "answer": answer,
        "citations": citations,
        "route": final.get("route", "document_qa"),
        "latency_ms": latency_ms,
        "usage": usage,
    }
