"""Conversation history: reopen, rename, delete."""

from __future__ import annotations

import streamlit as st

from lib import api, ui

st.markdown("### Conversation history")

try:
    conversations = api.conversations()
except api.ApiError as exc:
    st.error(exc.message)
    st.stop()

if not conversations:
    st.info("No conversations yet. Start one from the Chat page.")
    st.stop()

for conversation in conversations:
    with st.expander(f"{conversation['title']} — updated {conversation['updated_at'][:16]}"):
        left, middle, right = st.columns([3, 1, 1])

        with left:
            new_title = st.text_input(
                "Title", conversation["title"], key=f"title_{conversation['id']}"
            )
        with middle:
            st.write("")
            if st.button("Rename", key=f"rename_{conversation['id']}"):
                try:
                    api.patch(
                        f"/conversations/{conversation['id']}", {"title": new_title}
                    )
                    st.success("Renamed.")
                    st.rerun()
                except api.ApiError as exc:
                    st.error(exc.message)
        with right:
            st.write("")
            if st.button("Delete", key=f"delete_{conversation['id']}", type="secondary"):
                try:
                    api.delete(f"/conversations/{conversation['id']}")
                    st.success("Deleted.")
                    st.rerun()
                except api.ApiError as exc:
                    st.error(exc.message)

        if st.button("Open transcript", key=f"open_{conversation['id']}"):
            try:
                for message in api.messages(conversation["id"]):
                    ui.render_message(message)
            except api.ApiError as exc:
                st.error(exc.message)
