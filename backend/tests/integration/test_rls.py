"""Cross-department isolation, proved against a real database.

This is the most important test in the repository. Everything else is a
feature; this is the promise. It runs only when TEST_DB_URL points at a
database with the migrations applied — never against dev or prod.

    TEST_DB_URL=postgresql://... pytest backend/tests/integration -v
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DB_URL = os.getenv("TEST_DB_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DB_URL is not set"),
    pytest.mark.asyncio,
]


async def _connect():
    import asyncpg

    return await asyncpg.connect(TEST_DB_URL, statement_cache_size=0)


async def _as_user(conn, user_id: str):
    """Mirror exactly what app.db.pool.rls_conn does per request."""
    import json

    await conn.execute(
        "select set_config('request.jwt.claims', $1, true)",
        json.dumps({"sub": str(user_id), "role": "authenticated"}),
    )
    await conn.execute("set local role authenticated")


@pytest.fixture
async def seeded():
    """Two departments, two members, one document and chunk each."""
    conn = await _connect()
    data: dict[str, uuid.UUID] = {
        "dept_a": uuid.uuid4(), "dept_b": uuid.uuid4(),
        "user_a": uuid.uuid4(), "user_b": uuid.uuid4(),
        "doc_a": uuid.uuid4(), "doc_b": uuid.uuid4(),
    }
    tx = conn.transaction()
    await tx.start()
    try:
        for key, dept in (("dept_a", "alpha"), ("dept_b", "beta")):
            await conn.execute(
                "insert into departments (id, name, slug) values ($1, $2, $3)",
                data[key], dept.title(), f"{dept}-{data[key].hex[:8]}",
            )
        for user_key, dept_key in (("user_a", "dept_a"), ("user_b", "dept_b")):
            await conn.execute(
                "insert into auth.users (id, email) values ($1, $2)",
                data[user_key], f"{user_key}-{data[user_key].hex[:8]}@test.local",
            )
            await conn.execute(
                "insert into profiles (id, email, role) values ($1, $2, 'user')",
                data[user_key], f"{user_key}-{data[user_key].hex[:8]}@test.local",
            )
            await conn.execute(
                "insert into user_departments (user_id, department_id) values ($1, $2)",
                data[user_key], data[dept_key],
            )
        for doc_key, dept_key in (("doc_a", "dept_a"), ("doc_b", "dept_b")):
            await conn.execute(
                """
                insert into documents (id, department_id, filename, storage_path,
                                       mime_type, size_bytes, checksum_sha256, status)
                values ($1, $2, 'secret.pdf', 'p', 'application/pdf', 10, $3, 'ready')
                """,
                data[doc_key], data[dept_key], data[doc_key].hex,
            )
            await conn.execute(
                """
                insert into document_chunks (document_id, department_id, chunk_index,
                                             content, embedding)
                values ($1, $2, 0, $3, $4::vector)
                """,
                data[doc_key], data[dept_key],
                f"confidential content for {doc_key}",
                "[" + ",".join(["0.01"] * 1536) + "]",
            )
        yield conn, data
    finally:
        await tx.rollback()
        await conn.close()


async def test_member_sees_only_their_own_department(seeded):
    conn, data = seeded
    await _as_user(conn, data["user_a"])
    rows = await conn.fetch("select id from departments")
    ids = {r["id"] for r in rows}
    assert data["dept_a"] in ids
    assert data["dept_b"] not in ids


async def test_member_cannot_read_another_departments_documents(seeded):
    conn, data = seeded
    await _as_user(conn, data["user_a"])
    row = await conn.fetchrow("select id from documents where id = $1", data["doc_b"])
    assert row is None


async def test_member_cannot_read_another_departments_chunks(seeded):
    """Even naming the row directly returns nothing. The filter is in the
    database, so an application bug cannot widen it."""
    conn, data = seeded
    await _as_user(conn, data["user_a"])
    rows = await conn.fetch(
        "select id from document_chunks where department_id = $1", data["dept_b"]
    )
    assert rows == []


async def test_hybrid_search_cannot_cross_a_department(seeded):
    conn, data = seeded
    await _as_user(conn, data["user_a"])
    rows = await conn.fetch(
        "select chunk_id from hybrid_search($1, $2::vector, $3, 10, 30)",
        data["dept_b"],
        "[" + ",".join(["0.01"] * 1536) + "]",
        "confidential",
    )
    assert rows == []


async def test_search_within_own_department_still_works(seeded):
    """Isolation that also blocks legitimate access is not a passing test."""
    conn, data = seeded
    await _as_user(conn, data["user_a"])
    rows = await conn.fetch(
        "select chunk_id from hybrid_search($1, $2::vector, $3, 10, 30)",
        data["dept_a"],
        "[" + ",".join(["0.01"] * 1536) + "]",
        "confidential",
    )
    assert len(rows) == 1


async def test_member_cannot_grant_themselves_another_department(seeded):
    from asyncpg.exceptions import InsufficientPrivilegeError

    conn, data = seeded
    await _as_user(conn, data["user_a"])
    with pytest.raises(InsufficientPrivilegeError):
        await conn.execute(
            "insert into user_departments (user_id, department_id) values ($1, $2)",
            data["user_a"], data["dept_b"],
        )


async def test_member_cannot_read_another_users_conversations(seeded):
    conn, data = seeded
    await _as_user(conn, data["user_b"])
    conversation_id = await conn.fetchval(
        """
        insert into conversations (user_id, department_id, title)
        values ($1, $2, 'private') returning id
        """,
        data["user_b"], data["dept_b"],
    )
    await conn.execute("reset role")
    await _as_user(conn, data["user_a"])
    assert await conn.fetchrow(
        "select id from conversations where id = $1", conversation_id
    ) is None
