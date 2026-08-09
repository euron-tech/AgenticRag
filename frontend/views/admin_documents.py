"""Admin: upload and manage documents per department."""

from __future__ import annotations

import streamlit as st

from lib import api, auth, ui

if not auth.is_admin():
    st.error("Administrator access is required.")
    st.stop()

st.markdown("### Documents")

try:
    departments = api.get("/admin/departments") or []
    supported = (api.get("/documents/supported-types") or {}).get("extensions", [])
except api.ApiError as exc:
    st.error(exc.message)
    st.stop()

if not departments:
    st.info("Create a department first, on the Departments page.")
    st.stop()

names = {d["name"]: d for d in departments}
chosen = st.selectbox("Department", list(names))
department = names[chosen]

st.divider()
st.markdown("#### Upload")
uploaded = st.file_uploader(
    "Choose a file",
    type=[ext.lstrip(".") for ext in supported] or None,
    accept_multiple_files=True,
)

if uploaded and st.button("Upload and process", type="primary"):
    for item in uploaded:
        try:
            api.upload(
                "/documents/upload",
                department_id=department["id"],
                filename=item.name,
                content=item.getvalue(),
            )
            st.success(f"{item.name} uploaded — processing has been queued.")
        except api.ApiError as exc:
            st.error(f"{item.name}: {exc.message}")
    st.rerun()

st.divider()
st.markdown("#### Indexed documents")

if st.button("Refresh"):
    st.rerun()

try:
    documents = api.documents(department["id"])
except api.ApiError as exc:
    st.error(exc.message)
    st.stop()

if not documents:
    st.info("No documents in this department yet.")
    st.stop()

for document in documents:
    with st.container(border=True):
        header, actions = st.columns([4, 1])
        with header:
            st.markdown(f"**{document['filename']}**")
            st.caption(
                f"{ui.status_badge(document['status'])} · "
                f"{ui.human_size(document['size_bytes'])} · "
                f"{document['chunk_count']} sections"
                + (f" · {document['page_count']} pages" if document.get("page_count") else "")
            )
            # A failed document says why, in words an admin can act on. This is
            # the difference between a fixable upload and a silent empty index.
            if document["status"] == "failed" and document.get("error_message"):
                st.error(document["error_message"])
            elif document["status"] in ("pending", "processing"):
                st.info("Processing. Refresh in a moment.")

        with actions:
            if st.button("Reprocess", key=f"re_{document['id']}", use_container_width=True):
                try:
                    api.post(f"/documents/{document['id']}/reprocess")
                    st.success("Queued.")
                    st.rerun()
                except api.ApiError as exc:
                    st.error(exc.message)

            if st.button("Delete", key=f"del_{document['id']}", use_container_width=True):
                try:
                    api.delete(f"/documents/{document['id']}")
                    st.success("Deleted.")
                    st.rerun()
                except api.ApiError as exc:
                    st.error(exc.message)
