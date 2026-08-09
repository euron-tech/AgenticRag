"""Data access. SQL lives here and nowhere else.

Every function takes an explicit connection so the caller decides whether the
query runs under RLS (`rls_conn`) or as machine work (`admin_conn`). These
functions never open their own connection — that decision is not theirs.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from app.db.pool import Conn, to_vector


def _uid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# ------------------------------------------------------------- departments
async def list_departments(conn: Conn) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select d.id, d.name, d.slug, d.description, d.is_active,
               (select count(*) from documents doc
                 where doc.department_id = d.id and doc.status = 'ready') as document_count
        from departments d
        where d.is_active
        order by d.name
        """
    )
    return [dict(r) for r in rows]


async def create_department(
    conn: Conn, *, name: str, slug: str, description: str | None
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        insert into departments (name, slug, description)
        values ($1, $2, $3)
        returning id, name, slug, description, is_active
        """,
        name,
        slug,
        description,
    )
    return dict(row)


async def get_department(conn: Conn, department_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "select id, name, slug, description, is_active from departments where id = $1",
        _uid(department_id),
    )
    return dict(row) if row else None


# ---------------------------------------------------------------- profiles
async def list_profiles(conn: Conn) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select p.id, p.email, p.full_name, p.role, p.is_active, p.created_at,
               coalesce(
                   array_agg(ud.department_id) filter (where ud.department_id is not null),
                   '{}'
               ) as department_ids
        from profiles p
        left join user_departments ud on ud.user_id = p.id
        group by p.id
        order by p.created_at desc
        """
    )
    return [dict(r) for r in rows]


async def upsert_profile(
    conn: Conn,
    *,
    user_id: str,
    email: str,
    full_name: str | None,
    role: str,
) -> None:
    await conn.execute(
        """
        insert into profiles (id, email, full_name, role)
        values ($1, $2, $3, $4)
        on conflict (id) do update
           set email = excluded.email,
               full_name = excluded.full_name,
               role = excluded.role
        """,
        _uid(user_id),
        email,
        full_name,
        role,
    )


async def update_profile(
    conn: Conn,
    *,
    user_id: str,
    full_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> None:
    await conn.execute(
        """
        update profiles
           set full_name = coalesce($2, full_name),
               role      = coalesce($3, role),
               is_active = coalesce($4, is_active)
         where id = $1
        """,
        _uid(user_id),
        full_name,
        role,
        is_active,
    )


async def set_user_departments(
    conn: Conn, *, user_id: str, department_ids: Sequence[str], granted_by: str | None
) -> None:
    await conn.execute("delete from user_departments where user_id = $1", _uid(user_id))
    if not department_ids:
        return
    await conn.executemany(
        """
        insert into user_departments (user_id, department_id, granted_by)
        values ($1, $2, $3)
        on conflict do nothing
        """,
        [
            (_uid(user_id), _uid(d), _uid(granted_by) if granted_by else None)
            for d in department_ids
        ],
    )


# --------------------------------------------------------------- documents
async def create_document(
    conn: Conn,
    *,
    department_id: str,
    filename: str,
    storage_path: str,
    mime_type: str,
    size_bytes: int,
    checksum: str,
    uploaded_by: str | None,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        insert into documents (department_id, filename, storage_path, mime_type,
                               size_bytes, checksum_sha256, uploaded_by)
        values ($1, $2, $3, $4, $5, $6, $7)
        returning id, department_id, filename, mime_type, size_bytes, status,
                  error_message, page_count, chunk_count, created_at, processed_at
        """,
        _uid(department_id),
        filename,
        storage_path,
        mime_type,
        size_bytes,
        checksum,
        _uid(uploaded_by) if uploaded_by else None,
    )
    return dict(row)


async def find_duplicate(
    conn: Conn, *, department_id: str, checksum: str
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "select id, filename from documents where department_id = $1 and checksum_sha256 = $2",
        _uid(department_id),
        checksum,
    )
    return dict(row) if row else None


async def list_documents(conn: Conn, department_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select id, department_id, filename, mime_type, size_bytes, status,
               error_message, page_count, chunk_count, created_at, processed_at
        from documents
        where department_id = $1
        order by created_at desc
        """,
        _uid(department_id),
    )
    return [dict(r) for r in rows]


async def get_document(conn: Conn, document_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        select id, department_id, filename, storage_path, mime_type, size_bytes,
               status, error_message, page_count, chunk_count, created_at, processed_at
        from documents where id = $1
        """,
        _uid(document_id),
    )
    return dict(row) if row else None


async def set_document_status(
    conn: Conn,
    *,
    document_id: str,
    status: str,
    error_message: str | None = None,
    page_count: int | None = None,
    chunk_count: int | None = None,
) -> None:
    await conn.execute(
        """
        update documents
           set status = $2,
               error_message = $3,
               page_count = coalesce($4, page_count),
               chunk_count = coalesce($5, chunk_count),
               processed_at = case when $2 in ('ready', 'failed') then now() else processed_at end
         where id = $1
        """,
        _uid(document_id),
        status,
        error_message,
        page_count,
        chunk_count,
    )


async def delete_document(conn: Conn, document_id: str) -> None:
    # chunks and jobs cascade
    await conn.execute("delete from documents where id = $1", _uid(document_id))


# ------------------------------------------------------------------ chunks
async def delete_chunks(conn: Conn, document_id: str) -> None:
    await conn.execute("delete from document_chunks where document_id = $1", _uid(document_id))


async def insert_chunks(
    conn: Conn,
    *,
    document_id: str,
    department_id: str,
    chunks: Sequence[dict[str, Any]],
) -> int:
    if not chunks:
        return 0
    await conn.executemany(
        """
        insert into document_chunks
            (document_id, department_id, chunk_index, content, token_count, embedding, metadata)
        values ($1, $2, $3, $4, $5, $6::vector, $7::jsonb)
        on conflict (document_id, chunk_index) do update
           set content = excluded.content,
               token_count = excluded.token_count,
               embedding = excluded.embedding,
               metadata = excluded.metadata
        """,
        [
            (
                _uid(document_id),
                _uid(department_id),
                c["chunk_index"],
                c["content"],
                c["token_count"],
                to_vector(c["embedding"]),
                json.dumps(c.get("metadata", {})),
            )
            for c in chunks
        ],
    )
    return len(chunks)


async def hybrid_search(
    conn: Conn,
    *,
    department_id: str,
    embedding: Sequence[float],
    query: str,
    match_count: int,
    pool_size: int,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select chunk_id, document_id, filename, chunk_index, content, metadata, score
        from hybrid_search($1, $2::vector, $3, $4, $5)
        """,
        _uid(department_id),
        to_vector(embedding),
        query,
        match_count,
        pool_size,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        meta = d.get("metadata")
        d["metadata"] = json.loads(meta) if isinstance(meta, str) else (meta or {})
        out.append(d)
    return out


# ------------------------------------------------------------ ingestion jobs
async def enqueue_job(conn: Conn, document_id: str) -> None:
    await conn.execute(
        """
        insert into ingestion_jobs (document_id, state, attempts)
        values ($1, 'queued', 0)
        """,
        _uid(document_id),
    )


async def claim_job(conn: Conn, stale_seconds: int) -> dict[str, Any] | None:
    """Atomically take one job. SKIP LOCKED keeps replicas from colliding."""
    row = await conn.fetchrow(
        """
        with candidate as (
            select id from ingestion_jobs
             where state = 'queued'
                or (state = 'running'
                    and heartbeat_at < now() - make_interval(secs => $1))
             order by created_at
             limit 1
             for update skip locked
        )
        update ingestion_jobs j
           set state = 'running',
               attempts = j.attempts + 1,
               started_at = coalesce(j.started_at, now()),
               heartbeat_at = now()
          from candidate c
         where j.id = c.id
        returning j.id, j.document_id, j.attempts
        """,
        float(stale_seconds),
    )
    return dict(row) if row else None


async def heartbeat_job(conn: Conn, job_id: str) -> None:
    await conn.execute(
        "update ingestion_jobs set heartbeat_at = now() where id = $1", _uid(job_id)
    )


async def finish_job(
    conn: Conn, *, job_id: str, state: str, error: str | None = None
) -> None:
    await conn.execute(
        """
        update ingestion_jobs
           set state = $2, last_error = $3, finished_at = now()
         where id = $1
        """,
        _uid(job_id),
        state,
        error,
    )


async def requeue_job(conn: Conn, job_id: str, error: str) -> None:
    await conn.execute(
        "update ingestion_jobs set state = 'queued', last_error = $2 where id = $1",
        _uid(job_id),
        error,
    )


# ----------------------------------------------------------- conversations
async def create_conversation(
    conn: Conn, *, user_id: str, department_id: str, title: str
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        insert into conversations (user_id, department_id, title)
        values ($1, $2, $3)
        returning id, department_id, title, archived, created_at, updated_at
        """,
        _uid(user_id),
        _uid(department_id),
        title,
    )
    return dict(row)


async def list_conversations(
    conn: Conn, *, user_id: str, department_id: str | None = None
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select id, department_id, title, archived, created_at, updated_at
        from conversations
        where user_id = $1
          and ($2::uuid is null or department_id = $2::uuid)
          and not archived
        order by updated_at desc
        limit 100
        """,
        _uid(user_id),
        _uid(department_id) if department_id else None,
    )
    return [dict(r) for r in rows]


async def get_conversation(conn: Conn, conversation_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        select id, user_id, department_id, title, summary, archived, created_at, updated_at
        from conversations where id = $1
        """,
        _uid(conversation_id),
    )
    return dict(row) if row else None


async def rename_conversation(conn: Conn, *, conversation_id: str, title: str) -> None:
    await conn.execute(
        "update conversations set title = $2 where id = $1", _uid(conversation_id), title
    )


async def delete_conversation(conn: Conn, conversation_id: str) -> None:
    await conn.execute("delete from conversations where id = $1", _uid(conversation_id))


# ---------------------------------------------------------------- messages
async def list_messages(
    conn: Conn, *, conversation_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select id, role, content, citations, created_at
        from messages
        where conversation_id = $1
        order by created_at
        limit $2
        """,
        _uid(conversation_id),
        limit,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        cites = d.get("citations")
        d["citations"] = json.loads(cites) if isinstance(cites, str) else (cites or [])
        out.append(d)
    return out


async def insert_message(
    conn: Conn,
    *,
    conversation_id: str,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
) -> str:
    row = await conn.fetchrow(
        """
        insert into messages (conversation_id, role, content, citations, usage, latency_ms)
        values ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
        returning id
        """,
        _uid(conversation_id),
        role,
        content,
        json.dumps(citations or []),
        json.dumps(usage or {}),
        latency_ms,
    )
    return str(row["id"])


# ------------------------------------------------------------------- audit
async def write_audit(
    conn: Conn,
    *,
    actor_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    await conn.execute(
        """
        insert into audit_log (actor_id, action, entity_type, entity_id, payload, ip)
        values ($1, $2, $3, $4, $5::jsonb, $6)
        """,
        _uid(actor_id) if actor_id else None,
        action,
        entity_type,
        _uid(entity_id) if entity_id else None,
        json.dumps(payload or {}),
        ip,
    )


async def list_audit(conn: Conn, limit: int = 200) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select a.id, p.email as actor_email, a.action, a.entity_type,
               a.entity_id, a.payload, a.created_at
        from audit_log a
        left join profiles p on p.id = a.actor_id
        order by a.created_at desc
        limit $1
        """,
        limit,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        payload = d.get("payload")
        d["payload"] = json.loads(payload) if isinstance(payload, str) else (payload or {})
        out.append(d)
    return out
