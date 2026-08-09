"""End-to-end processing of one document.

Runs as machine work on an RLS-bypassing connection. The one rule that matters
here: a document is only ever marked `ready` if it produced real chunks. A
document that yielded nothing is `failed` with a reason a person can act on,
never a silently empty index.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.errors import NotFound, ValidationFailed
from app.core.logging import get_logger
from app.core.metrics import emit, timed
from app.db import repositories as repo
from app.db.pool import admin_conn
from app.ingestion import loaders
from app.ingestion.chunking import chunk_units
from app.ingestion.embedding import embed_texts
from app.services import supabase

log = get_logger(__name__)


async def process_document(document_id: str) -> int:
    """Parse, chunk, embed, and index one document. Returns the chunk count.

    Raises ValidationFailed for permanent problems (unreadable file, no text),
    which the worker treats as terminal. Anything else is retryable.
    """
    async with admin_conn() as conn:
        document = await repo.get_document(conn, document_id)
        if document is None:
            raise NotFound("Document not found.")
        await repo.set_document_status(
            conn, document_id=document_id, status="processing", error_message=None
        )

    filename = document["filename"]
    log.info("ingestion_started", extra={"document_id": document_id, "filename": filename})

    with timed("IngestionDuration", properties={"document_id": document_id}) as span:
        data = await supabase.download(document["storage_path"])
        result = loaders.load(filename, data)

        total_chars = sum(len(unit.text) for unit in result.units)
        _assert_extraction_worked(result, total_chars)

        chunks = chunk_units(result.units)
        if not chunks:
            raise ValidationFailed(
                "The file was read but produced no indexable text."
            )

        vectors = await embed_texts([c.content for c in chunks])

        payload = [
            {
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "embedding": vector,
                "metadata": chunk.metadata,
            }
            for chunk, vector in zip(chunks, vectors, strict=False)
        ]

        async with admin_conn() as conn:
            async with conn.transaction():
                # Replace rather than append, so a re-process never doubles a document.
                await repo.delete_chunks(conn, document_id)
                await repo.insert_chunks(
                    conn,
                    document_id=document_id,
                    department_id=str(document["department_id"]),
                    chunks=payload,
                )
                await repo.set_document_status(
                    conn,
                    document_id=document_id,
                    status="ready",
                    error_message=None,
                    page_count=result.page_count,
                    chunk_count=len(chunks),
                )

        span["chunks"] = len(chunks)
        span["source_type"] = result.source_type

    emit(
        {"DocumentsIndexed": 1.0, "ChunksIndexed": float(len(chunks))},
        unit="Count",
        dimensions={"SourceType": result.source_type},
    )
    log.info(
        "ingestion_complete",
        extra={"document_id": document_id, "chunks": len(chunks),
               "source_type": result.source_type},
    )
    return len(chunks)


def _assert_extraction_worked(result: loaders.LoadResult, total_chars: int) -> None:
    """Catch the quiet failure: a file that parsed fine and yielded nothing.

    Scanned PDFs are the usual cause. Left unchecked they index as an empty
    document and the assistant answers 'not found' forever with no explanation.
    """
    if not result.units or total_chars == 0:
        if result.source_type == "pdf":
            raise ValidationFailed(
                "No extractable text found. This is almost certainly a scanned or "
                "image-only PDF. Upload a text-based version, or run OCR on it first."
            )
        raise ValidationFailed(
            "No text could be extracted from this file. It may be empty or corrupt."
        )

    pages = result.page_count or 0
    if result.source_type == "pdf" and pages > 0:
        floor = settings.min_chars_per_page * pages
        if total_chars < floor:
            raise ValidationFailed(
                f"Only {total_chars} characters were extracted across {pages} pages, "
                "which is far below what a text PDF produces. This is likely a scanned "
                "document. Upload a text-based version, or run OCR on it first."
            )
