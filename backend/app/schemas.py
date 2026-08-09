"""API request and response models. No bare dicts cross an API boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# ------------------------------------------------------------------ auth
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class Profile(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    role: Literal["admin", "user"]
    is_active: bool = True
    department_ids: list[str] = Field(default_factory=list)


class LoginResponse(BaseModel):
    access_token: str
    expires_at: int | None = None
    profile: Profile


# ----------------------------------------------------------- departments
class Department(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    is_active: bool = True
    document_count: int = 0


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)


# -------------------------------------------------------------- documents
DocumentStatus = Literal["pending", "processing", "ready", "failed"]


class Document(BaseModel):
    id: str
    department_id: str
    filename: str
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    error_message: str | None = None
    page_count: int | None = None
    chunk_count: int = 0
    created_at: datetime
    processed_at: datetime | None = None


# ------------------------------------------------------------------ chat
class Citation(BaseModel):
    marker: int
    document_id: str
    filename: str
    chunk_id: str
    location: str
    snippet: str
    score: float | None = None


class ChatRequest(BaseModel):
    department_id: str
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    route: str
    latency_ms: int
    usage: dict[str, Any] = Field(default_factory=dict)


class Conversation(BaseModel):
    id: str
    department_id: str
    title: str
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: datetime


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


# ----------------------------------------------------------------- admin
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str | None = Field(default=None, max_length=120)
    role: Literal["admin", "user"] = "user"
    department_ids: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
    department_ids: list[str] | None = None


class AuditEntry(BaseModel):
    id: str
    actor_email: str | None = None
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Ok(BaseModel):
    ok: bool = True
