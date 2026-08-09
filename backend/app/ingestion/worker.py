"""In-process ingestion worker.

A durable queue in Postgres rather than a separate service: claims are atomic
(`for update skip locked`) so extra replicas are safe, and a heartbeat means a
task killed mid-ingest has its job reclaimed instead of stranding a document in
`processing` forever.

If throughput ever outgrows this, the same `ingestion_jobs` table can be fed
from SQS by a dedicated worker service without touching the API surface.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.config import settings
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.core.metrics import emit
from app.db import repositories as repo
from app.db.pool import admin_conn
from app.ingestion.pipeline import process_document

log = get_logger(__name__)

HEARTBEAT_SECONDS = 30


async def _heartbeat(job_id: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        if stop.is_set():
            return
        try:
            async with admin_conn() as conn:
                await repo.heartbeat_job(conn, job_id)
        except Exception:
            log.warning("heartbeat_failed", extra={"job_id": job_id})


async def _handle(job: dict) -> None:
    job_id = str(job["id"])
    document_id = str(job["document_id"])
    attempts = int(job["attempts"])

    stop = asyncio.Event()
    beat = asyncio.create_task(_heartbeat(job_id, stop))
    try:
        await process_document(document_id)
        async with admin_conn() as conn:
            await repo.finish_job(conn, job_id=job_id, state="done")
    except ValidationFailed as exc:
        # Permanent: the file itself is the problem. Retrying cannot help.
        await _fail(job_id, document_id, str(exc))
        emit({"IngestionFailures": 1.0}, unit="Count", dimensions={"Reason": "permanent"})
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if attempts >= settings.worker_max_attempts:
            await _fail(job_id, document_id, f"Failed after {attempts} attempts. {message}")
            emit({"IngestionFailures": 1.0}, unit="Count", dimensions={"Reason": "exhausted"})
        else:
            log.warning(
                "ingestion_retry",
                extra={"document_id": document_id, "attempt": attempts, "detail": message},
            )
            async with admin_conn() as conn:
                await repo.requeue_job(conn, job_id, message)
                await repo.set_document_status(
                    conn, document_id=document_id, status="pending", error_message=None
                )
    finally:
        stop.set()
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat


async def _fail(job_id: str, document_id: str, message: str) -> None:
    log.error("ingestion_failed", extra={"document_id": document_id, "detail": message})
    async with admin_conn() as conn:
        await repo.finish_job(conn, job_id=job_id, state="failed", error=message)
        await repo.set_document_status(
            conn, document_id=document_id, status="failed", error_message=message
        )


async def run_worker(stop: asyncio.Event) -> None:
    log.info("ingestion_worker_started")
    while not stop.is_set():
        try:
            async with admin_conn() as conn:
                job = await repo.claim_job(conn, settings.worker_stale_seconds)
        except Exception:
            log.exception("worker_claim_failed")
            job = None

        if job is None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            continue

        await _handle(job)

    log.info("ingestion_worker_stopped")
