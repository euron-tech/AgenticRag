"""Chat and conversation history."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUserDep, require_department
from app.core.errors import Forbidden, NotFound
from app.db import repositories as repo
from app.db.pool import rls_conn
from app.schemas import (
    ChatRequest,
    ChatResponse,
    Conversation,
    ConversationRename,
    Message,
    Ok,
)
from app.services import chat as chat_service

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, user: CurrentUserDep) -> ChatResponse:
    require_department(user, payload.department_id)
    result = await chat_service.answer_question(
        user=user,
        department_id=payload.department_id,
        message=payload.message,
        conversation_id=payload.conversation_id,
    )
    return ChatResponse(**result)


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(
    user: CurrentUserDep, department_id: str | None = None
) -> list[Conversation]:
    if department_id:
        require_department(user, department_id)
    async with rls_conn(user.id) as conn:
        rows = await repo.list_conversations(
            conn, user_id=user.id, department_id=department_id
        )
    return [
        Conversation(
            id=str(r["id"]),
            department_id=str(r["department_id"]),
            title=r["title"],
            archived=r["archived"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


async def _owned_conversation(user_id: str, conversation_id: str) -> dict:
    async with rls_conn(user_id) as conn:
        conversation = await repo.get_conversation(conn, conversation_id)
    if conversation is None:
        raise NotFound("Conversation not found.")
    if str(conversation["user_id"]) != user_id:
        raise Forbidden("This conversation belongs to another user.")
    return conversation


@router.get("/conversations/{conversation_id}/messages", response_model=list[Message])
async def list_messages(conversation_id: str, user: CurrentUserDep) -> list[Message]:
    await _owned_conversation(user.id, conversation_id)
    async with rls_conn(user.id) as conn:
        rows = await repo.list_messages(conn, conversation_id=conversation_id)
    return [
        Message(
            id=str(r["id"]),
            role=r["role"],
            content=r["content"],
            citations=r["citations"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.patch("/conversations/{conversation_id}", response_model=Ok)
async def rename_conversation(
    conversation_id: str, payload: ConversationRename, user: CurrentUserDep
) -> Ok:
    await _owned_conversation(user.id, conversation_id)
    async with rls_conn(user.id) as conn:
        await repo.rename_conversation(
            conn, conversation_id=conversation_id, title=payload.title
        )
    return Ok()


@router.delete("/conversations/{conversation_id}", response_model=Ok)
async def delete_conversation(conversation_id: str, user: CurrentUserDep) -> Ok:
    await _owned_conversation(user.id, conversation_id)
    async with rls_conn(user.id) as conn:
        await repo.delete_conversation(conn, conversation_id)
    return Ok()
