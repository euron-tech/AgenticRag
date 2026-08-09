"""Chat with a department's documents."""

from __future__ import annotations

import streamlit as st

from lib import api, auth, ui

department = auth.department_picker()
if department is None:
    st.stop()

department_id = department["id"]

# Switching department starts a new thread — a conversation is scoped to one
# department on the server, so carrying the id across would be rejected.
if st.session_state.get("active_department") != department_id:
    st.session_state["active_department"] = department_id
    st.session_state["conversation_id"] = None
    st.session_state["messages"] = []

st.markdown(f"### {department['name']}")
st.caption(
    f"{department.get('document_count', 0)} document(s) indexed. "
    "Answers come only from these documents, with sources."
)

with st.sidebar:
    if st.button("New conversation", use_container_width=True):
        st.session_state["conversation_id"] = None
        st.session_state["messages"] = []
        st.rerun()

    st.divider()
    st.caption("Recent conversations")
    try:
        recent = api.conversations(department_id)[:10]
    except api.ApiError as exc:
        recent = []
        st.error(exc.message)

    for conversation in recent:
        if st.button(
            conversation["title"],
            key=f"conv_{conversation['id']}",
            use_container_width=True,
        ):
            st.session_state["conversation_id"] = conversation["id"]
            try:
                st.session_state["messages"] = api.messages(conversation["id"])
            except api.ApiError as exc:
                st.error(exc.message)
            st.rerun()

for message in st.session_state.get("messages", []):
    ui.render_message(message)

question = st.chat_input("Ask something about this department's documents…")
if not question:
    st.stop()

st.session_state.setdefault("messages", []).append(
    {"role": "user", "content": question, "citations": []}
)
with st.chat_message("user"):
    st.markdown(question)

with st.chat_message("assistant"):
    with st.spinner("Searching the documents…"):
        try:
            result = api.ask(
                department_id, question, st.session_state.get("conversation_id")
            )
        except api.ApiError as exc:
            st.error(exc.message)
            st.stop()

    st.session_state["conversation_id"] = result["conversation_id"]
    st.markdown(result["answer"])
    ui.render_citations(result.get("citations") or [])
    st.caption(f"{result['route']} · {result['latency_ms']} ms")

st.session_state["messages"].append(
    {
        "role": "assistant",
        "content": result["answer"],
        "citations": result.get("citations") or [],
    }
)
