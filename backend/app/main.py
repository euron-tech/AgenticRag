"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import admin, auth, chat, documents, health
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger, set_request_context
from app.core.metrics import emit
from app.db import pool
from app.ingestion.worker import run_worker
from app.services import supabase

configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await pool.init_pool()
    await supabase.ensure_bucket()

    stop = asyncio.Event()
    worker: asyncio.Task | None = None
    if settings.worker_enabled:
        worker = asyncio.create_task(run_worker(stop))

    log.info("startup_complete", extra={"env": settings.app_env,
                                        "worker": settings.worker_enabled})
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        await supabase.close()
        await pool.close_pool()
        log.info("shutdown_complete")


app = FastAPI(
    title="Agentic RAG API",
    version="1.0.0",
    description="Department-scoped document intelligence.",
    lifespan=lifespan,
    docs_url=None if settings.is_prod else "/docs",
    redoc_url=None,
)

# The Streamlit frontend calls this API server-side, so no browser origin needs
# access. Kept narrow deliberately.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handlers(app)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    set_request_context(request_id=request_id, user_id=None)
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        log.exception("request_failed", extra={"path": request.url.path,
                                               "method": request.method})
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error",
                               "message": "An unexpected error occurred.",
                               "request_id": request_id}},
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["x-request-id"] = request_id

    if request.url.path not in ("/health", "/api/v1/health"):
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(elapsed_ms, 2),
            },
        )
        emit(
            {"RequestDuration": elapsed_ms},
            unit="Milliseconds",
            dimensions={"Status": str(response.status_code // 100) + "xx"},
        )
    return response


API = "/api/v1"
app.include_router(health.router)
app.include_router(health.router, prefix=API)
app.include_router(auth.router, prefix=API)
app.include_router(documents.router, prefix=API)
app.include_router(chat.router, prefix=API)
app.include_router(admin.router, prefix=API)
