from __future__ import annotations

from app.core.config import settings
from app.ingestion.chunking import chunk_units
from app.ingestion.loaders import ParsedUnit


def unit(text: str, group: str = "page:1", location: str = "Page 1", atomic: bool = False):
    return ParsedUnit(text=text, group=group, location=location,
                      metadata={"page": 1}, atomic=atomic)


def test_small_units_in_same_group_are_packed_together():
    chunks = chunk_units([unit("alpha"), unit("beta"), unit("gamma")])
    assert len(chunks) == 1
    assert "alpha" in chunks[0].content and "gamma" in chunks[0].content


def test_units_from_different_groups_never_merge():
    """Merging across pages would make the citation point at two places."""
    chunks = chunk_units(
        [unit("page one text", group="page:1", location="Page 1"),
         unit("page two text", group="page:2", location="Page 2")]
    )
    assert len(chunks) == 2
    assert chunks[0].metadata["location"] == "Page 1"
    assert chunks[1].metadata["location"] == "Page 2"


def test_atomic_units_stay_alone():
    """A table block carries its header; packing another block in would put two
    headers in one chunk."""
    chunks = chunk_units([unit("table a", atomic=True), unit("table b", atomic=True)])
    assert len(chunks) == 2


def test_oversized_unit_is_split_with_overlap_and_labelled():
    text = ("sentence. " * (settings.chunk_chars // 5))
    chunks = chunk_units([unit(text)])
    assert len(chunks) > 1
    assert all(len(c.content) <= settings.chunk_chars for c in chunks)
    assert "part" in chunks[0].metadata
    assert "part 1/" in chunks[0].metadata["location"]


def test_chunk_indexes_are_monotonic_from_zero():
    chunks = chunk_units(
        [unit(f"text {i}", group=f"page:{i}", location=f"Page {i}") for i in range(5)]
    )
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_empty_and_whitespace_units_are_dropped():
    assert chunk_units([unit("   "), unit("")]) == []


def test_every_chunk_carries_a_location_for_citation():
    chunks = chunk_units([unit("content here")])
    assert chunks and chunks[0].metadata.get("location")
