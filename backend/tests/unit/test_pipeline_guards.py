"""The quiet-failure guard: a file that parses cleanly and yields nothing.

If this check ever regresses, scanned PDFs index as empty documents and the
assistant answers "not found" forever with no explanation anywhere.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailed
from app.ingestion.loaders import LoadResult, ParsedUnit
from app.ingestion.pipeline import _assert_extraction_worked


def test_pdf_with_no_text_is_reported_as_probably_scanned():
    result = LoadResult(units=[], page_count=12, source_type="pdf")
    with pytest.raises(ValidationFailed) as exc:
        _assert_extraction_worked(result, 0)
    assert "scanned" in str(exc.value).lower()


def test_pdf_with_token_amounts_of_text_is_still_treated_as_scanned():
    """Scanned PDFs often carry a few stray characters per page from artifacts."""
    result = LoadResult(
        units=[ParsedUnit(text="1", group="page:1", location="Page 1")],
        page_count=40,
        source_type="pdf",
    )
    with pytest.raises(ValidationFailed) as exc:
        _assert_extraction_worked(result, 20)
    assert "scanned" in str(exc.value).lower()


def test_empty_non_pdf_gets_a_generic_reason():
    result = LoadResult(units=[], page_count=None, source_type="docx")
    with pytest.raises(ValidationFailed) as exc:
        _assert_extraction_worked(result, 0)
    assert "No text could be extracted" in str(exc.value)


def test_a_healthy_text_pdf_passes():
    result = LoadResult(
        units=[ParsedUnit(text="x" * 5000, group="page:1", location="Page 1")],
        page_count=3,
        source_type="pdf",
    )
    _assert_extraction_worked(result, 5000)
