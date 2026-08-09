"""Postgres access.

Two deliberately different paths:

* `rls_conn(user_id)` — switches the session to the `authenticated` role and
  installs the caller's JWT claims, so every RLS policy applies. Use this for
  anything driven by a user request.
* `admin_conn()` — runs as the connection's own role (table owner), which
  bypasses RLS. Use this only for machine work: the ingestion worker, account
  provisioning, audit writes.

Choosing `admin_conn` for a user-facing read is how a cross-department leak
happens. The default is `rls_conn`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Union

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# A pool hands out proxies, not raw Connections; both satisfy the query API.
Conn = Union[asyncpg.Connection, "asyncpg.pool.PoolConnectionProxy"]

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    if _pool is not None:
        return
    _pool = await asyncpg.create_pool(
        dsn=settings.supabase_db_url,
        min_size=1,
        max_size=10,
        command_timeout=60,
        # Supabase sits behind a pooler; caching prepared statements breaks
        # under transaction pooling. Disabling costs a parse per query and
        # removes a whole class of "prepared statement already exists" errors.
        statement_cache_size=0,
    )
    log.info("db_pool_ready")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("db_pool_closed")


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised")
    return _pool


@asynccontextmanager
async def admin_conn() -> AsyncIterator[Conn]:
    """RLS-bypassing connection. Machine work only."""
    async with _require_pool().acquire() as conn:
        yield conn


@asynccontextmanager
async def rls_conn(user_id: str) -> AsyncIterator[Conn]:
    """RLS-enforced connection scoped to one user, inside a transaction."""
    async with _require_pool().acquire() as conn:
        async with conn.transaction():
            claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
            # is_local = true, so both settings unwind when the transaction ends
            await conn.execute("select set_config('request.jwt.claims', $1, true)", claims)
            await conn.execute("set local role authenticated")
            yield conn


async def healthcheck() -> bool:
    try:
        async with admin_conn() as conn:
            await conn.fetchval("select 1")
        return True
    except Exception:
        log.exception("db_healthcheck_failed")
        return False


def to_vector(values: Sequence[float]) -> str:
    """pgvector literal. Paired with an explicit ``$n::vector`` cast in SQL."""
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def as_dicts(rows: Sequence[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
