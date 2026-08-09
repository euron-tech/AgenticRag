"""Hybrid retrieval: dense + keyword, fused, department-scoped.

The department filter is applied inside SQL by `hybrid_search`, under RLS.
Nothing here filters in Python — that would mean a wider result set existed in
memory first, which is exactly the leak this design prevents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.metrics import timed
from app.db import repositories as repo
from app.db.pool import Conn
from app.ingestion.embedding import embed_query


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    content: str
    location: str
    score: float
    metadata: dict[str, Any]

    def as_citation(self, marker: int) -> dict[str, Any]:
        snippet = self.content.strip().replace("\n", " ")
        return {
            "marker": marker,
            "document_id": self.document_id,
            "filename": self.filename,
            "chunk_id": self.chunk_id,
            "location": self.location,
            "snippet": snippet[:400] + ("…" if len(snippet) > 400 else ""),
            "score": round(self.score, 5),
        }


async def retrieve(
    conn: Conn,
    *,
    department_id: str,
    query: str,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    top_k = top_k or settings.retrieval_top_k

    with timed("RetrievalDuration") as span:
        embedding = await embed_query(query)
        rows = await repo.hybrid_search(
            conn,
            department_id=department_id,
            embedding=embedding,
            query=query,
            match_count=top_k,
            pool_size=settings.candidate_pool,
        )
        span["results"] = len(rows)

    return [
        RetrievedChunk(
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            filename=row["filename"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            location=str(row["metadata"].get("location") or f"Chunk {row['chunk_index'] + 1}"),
            score=float(row["score"]),
            metadata=row["metadata"],
        )
        for row in rows
    ]


def build_context(chunks: list[RetrievedChunk]) -> str:
    """Numbered, delimited context blocks.

    The markers are what the model cites, and what we map back to chunk ids
    afterwards. Content is fenced and labelled as data so instructions written
    inside a document cannot be mistaken for instructions from the operator.
    """
    blocks: list[str] = []
    for marker, chunk in enumerate(chunks, start=1):
        blocks.append(
            f"<source id=\"{marker}\" document=\"{chunk.filename}\" "
            f"location=\"{chunk.location}\">\n{chunk.content}\n</source>"
        )
    return "\n\n".join(blocks)
