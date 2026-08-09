"""Upload validation: extension, magic bytes, and size.

Extension alone is a claim made by the uploader. Checking the leading bytes
catches a renamed executable before it ever reaches storage.
"""

from __future__ import annotations

import hashlib

from app.core.config import settings
from app.core.errors import ValidationFailed
from app.ingestion.loaders import MIME_TYPES, SUPPORTED_EXTENSIONS, extension_of

_ZIP = b"PK\x03\x04"

# Formats with a recognisable header. Text-ish formats have none by nature and
# are validated by decoding instead.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".docx": (_ZIP,),
    ".xlsx": (_ZIP,),
    ".xlsm": (_ZIP,),
    ".pptx": (_ZIP,),
}

_TEXT_LIKE = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".log"}


def validate_upload(filename: str, data: bytes) -> tuple[str, str, str]:
    """Return (extension, mime_type, sha256) or raise ValidationFailed."""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise ValidationFailed("Invalid file name.")

    ext = extension_of(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValidationFailed(
            f"Unsupported file type '{ext or filename}'. "
            f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    if not data:
        raise ValidationFailed("The file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise ValidationFailed(
            f"File is {len(data) / 1_048_576:.1f} MB. The limit is "
            f"{settings.max_upload_mb} MB."
        )

    expected = _MAGIC.get(ext)
    if expected and not any(data.startswith(prefix) for prefix in expected):
        raise ValidationFailed(
            f"This file does not look like a real {ext} file. Its contents do not "
            "match its extension."
        )

    if ext in _TEXT_LIKE:
        try:
            data[:4096].decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationFailed(
                f"A {ext} file must be UTF-8 text. This one is binary."
            ) from None

    return ext, MIME_TYPES.get(ext, "application/octet-stream"), hashlib.sha256(data).hexdigest()
