"""Chunking.

Units from the same group are packed together up to the character budget;
units from different groups never merge, so a citation always points at one
page, sheet range, slide, or section. Oversized units are split with overlap.

Sizes are in characters. A character budget needs no tokeniser download at
container start; ~4 chars per token is the working approximation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.ingestion.loaders import ParsedUnit

_BREAKS = ("\n\n", "\n", ". ", " ")


@dataclass
class Chunk:
    chunk_index: int
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _split_oversized(text: str, budget: int, overlap: int) -> list[str]:
    """Split on the largest natural boundary that fits, so chunks end at a
    paragraph or sentence rather than mid-word."""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + budget, len(text))
        if end < len(text):
            window = text[start:end]
            for token in _BREAKS:
                cut = window.rfind(token)
                if cut > budget // 2:
                    end = start + cut + len(token)
                    break
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def chunk_units(units: Sequence[ParsedUnit]) -> list[Chunk]:
    budget = settings.chunk_chars
    overlap = settings.chunk_overlap_chars

    chunks: list[Chunk] = []
    index = 0

    buffer: list[str] = []
    buffer_len = 0
    buffer_meta: dict[str, Any] | None = None
    buffer_group: str | None = None

    def flush() -> None:
        nonlocal buffer, buffer_len, buffer_meta, buffer_group, index
        if not buffer:
            return
        content = "\n\n".join(buffer).strip()
        buffer, buffer_len = [], 0
        if content:
            chunks.append(
                Chunk(
                    chunk_index=index,
                    content=content,
                    token_count=estimate_tokens(content),
                    metadata=dict(buffer_meta or {}),
                )
            )
            index += 1
        buffer_meta, buffer_group = None, None

    for unit in units:
        text = unit.text.strip()
        if not text:
            continue
        meta = {**unit.metadata, "location": unit.location}

        if unit.group != buffer_group:
            flush()

        if len(text) > budget:
            flush()
            parts = _split_oversized(text, budget, overlap)
            for part_no, part in enumerate(parts, start=1):
                part_meta = dict(meta)
                if len(parts) > 1:
                    part_meta["part"] = f"{part_no}/{len(parts)}"
                    part_meta["location"] = f"{unit.location} (part {part_no}/{len(parts)})"
                chunks.append(
                    Chunk(
                        chunk_index=index,
                        content=part,
                        token_count=estimate_tokens(part),
                        metadata=part_meta,
                    )
                )
                index += 1
            continue

        if unit.atomic or buffer_len + len(text) > budget:
            flush()

        if not buffer:
            buffer_meta = meta
            buffer_group = unit.group
        buffer.append(text)
        buffer_len += len(text) + 2

    flush()
    return chunks
