from __future__ import annotations

import hashlib

import pytest

from app.core.config import settings
from app.core.errors import ValidationFailed
from app.ingestion.validation import validate_upload


def test_accepts_a_real_pdf_header_and_returns_its_checksum():
    data = b"%PDF-1.7\n" + b"x" * 100
    ext, mime, checksum = validate_upload("report.pdf", data)
    assert ext == ".pdf"
    assert mime == "application/pdf"
    assert checksum == hashlib.sha256(data).hexdigest()


def test_rejects_a_file_whose_contents_do_not_match_its_extension():
    """A renamed binary is the case extension checks alone would let through."""
    with pytest.raises(ValidationFailed) as exc:
        validate_upload("payload.pdf", b"MZ\x90\x00 this is a windows executable")
    assert "does not look like a real" in str(exc.value)


def test_rejects_binary_content_in_a_text_extension():
    with pytest.raises(ValidationFailed):
        validate_upload("notes.txt", b"\xff\xfe\x00\x01\x02binary")


def test_rejects_path_traversal_in_the_filename():
    with pytest.raises(ValidationFailed):
        validate_upload("../../etc/passwd.txt", b"hello")


def test_rejects_empty_files():
    with pytest.raises(ValidationFailed) as exc:
        validate_upload("empty.txt", b"")
    assert "empty" in str(exc.value).lower()


def test_rejects_files_over_the_size_limit():
    oversized = b"a" * (settings.max_upload_bytes + 1)
    with pytest.raises(ValidationFailed) as exc:
        validate_upload("big.txt", oversized)
    assert "limit" in str(exc.value)


def test_accepts_zip_based_office_formats():
    ext, _, _ = validate_upload("sheet.xlsx", b"PK\x03\x04" + b"\x00" * 50)
    assert ext == ".xlsx"
