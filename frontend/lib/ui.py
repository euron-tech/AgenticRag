"""Shared rendering helpers."""

from __future__ import annotations

from typing import Any

import streamlit as st

STATUS_LABEL = {
    "pending": "⏳ Queued",
    "processing": "⚙️ Processing",
    "ready": "✅ Ready",
    "failed": "❌ Failed",
}


def status_badge(status: str) -> str:
    return STATUS_LABEL.get(status, status)


def render_citations(citations: list[dict[str, Any]]) -> None:
    """Sources are shown, not summarised away.

    An answer the reader cannot trace back to a document is not verifiable, and
    the whole point of this product is that it is.
    """
    if not citations:
        return
    with st.expander(f"Sources ({len(citations)})", expanded=False):
        for citation in citations:
            st.markdown(
                f"**[{citation['marker']}] {citation['filename']}** — "
                f"{citation.get('location', 'unknown location')}"
            )
            st.caption(citation.get("snippet", ""))
            st.divider()


def render_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        render_citations(message.get("citations") or [])


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
