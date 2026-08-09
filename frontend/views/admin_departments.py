"""Admin: create and review departments."""

from __future__ import annotations

import streamlit as st

from lib import api, auth

if not auth.is_admin():
    st.error("Administrator access is required.")
    st.stop()

st.markdown("### Departments")
st.caption("A department is the access boundary. Documents and answers never cross it.")

with st.form("create_department", clear_on_submit=True):
    name = st.text_input("Name", placeholder="Finance")
    description = st.text_area("Description", placeholder="Invoices, budgets, audit reports")
    if st.form_submit_button("Create department", type="primary"):
        if len(name.strip()) < 2:
            st.error("Give the department a name of at least two characters.")
        else:
            try:
                api.post(
                    "/admin/departments",
                    {"name": name.strip(), "description": description.strip() or None},
                )
                st.success(f"Created '{name.strip()}'.")
                st.rerun()
            except api.ApiError as exc:
                st.error(exc.message)

st.divider()

try:
    departments = api.get("/admin/departments") or []
except api.ApiError as exc:
    st.error(exc.message)
    st.stop()

if not departments:
    st.info("No departments yet.")
    st.stop()

st.dataframe(
    [
        {
            "Name": d["name"],
            "Slug": d["slug"],
            "Description": d.get("description") or "",
            "Ready documents": d.get("document_count", 0),
        }
        for d in departments
    ],
    use_container_width=True,
    hide_index=True,
)
