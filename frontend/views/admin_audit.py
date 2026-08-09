"""Admin: who did what."""

from __future__ import annotations

import streamlit as st

from lib import api, auth

if not auth.is_admin():
    st.error("Administrator access is required.")
    st.stop()

st.markdown("### Audit log")

limit = st.slider("Entries to show", 50, 500, 200, step=50)

try:
    entries = api.get("/admin/audit", {"limit": limit}) or []
except api.ApiError as exc:
    st.error(exc.message)
    st.stop()

if not entries:
    st.info("Nothing recorded yet.")
    st.stop()

st.dataframe(
    [
        {
            "When": entry["created_at"][:19].replace("T", " "),
            "Actor": entry.get("actor_email") or "system",
            "Action": entry["action"],
            "Entity": entry.get("entity_type") or "",
            "Details": ", ".join(f"{k}={v}" for k, v in (entry.get("payload") or {}).items()),
        }
        for entry in entries
    ],
    use_container_width=True,
    hide_index=True,
)
