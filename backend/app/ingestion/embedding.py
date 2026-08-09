"""OpenAI embeddings.

Batched with bounded retry. A transient 429 must never mark a 50-page document
failed, so retries happen per batch and only exhaustion propagates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from openai import APIError, AsyncOpenAI, RateLimitError

from app.core.config import settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.core.metrics import emit

log = get_logger(__name__)

_client: AsyncOpenAI | None = None

MAX_ATTEMPTS = 5


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0, max_retries=0)
    return _client


async def _embed_batch(texts: Sequence[str]) -> list[list[float]]:
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client().embeddings.create(
                model=settings.embedding_model,
                input=list(texts),
            )
            emit(
                {"EmbeddingTokens": float(response.usage.total_tokens)},
                unit="Count",
                dimensions={"Model": settings.embedding_model},
            )
            return [item.embedding for item in response.data]
        except (RateLimitError, APIError) as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            log.warning(
                "embedding_retry",
                extra={"attempt": attempt, "error_type": type(exc).__name__},
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20.0)

    raise UpstreamError(
        f"Embedding service failed after {MAX_ATTEMPTS} attempts: {last_error}"
    )


async def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    size = settings.embed_batch_size
    vectors: list[list[float]] = []
    for start in range(0, len(texts), size):
        batch = texts[start : start + size]
        vectors.extend(await _embed_batch(batch))

    if len(vectors) != len(texts):
        raise UpstreamError("Embedding service returned a mismatched number of vectors.")
    for vector in vectors:
        if len(vector) != settings.embedding_dim:
            raise UpstreamError(
                f"Embedding dimension mismatch: model returned {len(vector)}, "
                f"schema expects {settings.embedding_dim}. "
                "Changing the embedding model requires a migration and a full re-index."
            )
    return vectors


async def embed_query(text: str) -> list[float]:
    result = await embed_texts([text])
    return result[0]
