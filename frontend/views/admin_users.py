"""Admin: issue accounts and grant department access."""

from __future__ import annotations

import secrets
import string

import streamlit as st

from lib import api, auth

if not auth.is_admin():
    st.error("Administrator access is required.")
    st.stop()

st.markdown("### Users")
st.caption("Self-signup is disabled. Accounts exist because an administrator created them.")

try:
    departments = api.get("/admin/departments") or []
    users = api.get("/admin/users") or []
except api.ApiError as exc:
    st.error(exc.message)
    st.stop()

by_name = {d["name"]: d["id"] for d in departments}
by_id = {d["id"]: d["name"] for d in departments}


def suggest_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


st.markdown("#### Create an account")

if st.button("Suggest a strong password"):
    st.session_state["suggested_password"] = suggest_password()
if st.session_state.get("suggested_password"):
    st.code(st.session_state["suggested_password"], language=None)
    st.caption("Copy this now and hand it over securely — it is not stored anywhere.")

with st.form("create_user", clear_on_submit=True):
    email = st.text_input("Email")
    password = st.text_input("Password", type="password", help="At least 10 characters.")
    full_name = st.text_input("Full name")
    role = st.selectbox("Role", ["user", "admin"])
    granted = st.multiselect(
        "Departments",
        list(by_name),
        help="Administrators can reach every department regardless of this list.",
    )

    if st.form_submit_button("Create account", type="primary"):
        if len(password) < 10:
            st.error("Password must be at least 10 characters.")
        elif not email.strip():
            st.error("Enter an email address.")
        else:
            try:
                api.post(
                    "/admin/users",
                    {
                        "email": email.strip(),
                        "password": password,
                        "full_name": full_name.strip() or None,
                        "role": role,
                        "department_ids": [by_name[n] for n in granted],
                    },
                )
                st.success(f"Account created for {email.strip()}.")
                st.rerun()
            except api.ApiError as exc:
                st.error(exc.message)

st.divider()
st.markdown("#### Existing accounts")

if not users:
    st.info("No accounts yet.")
    st.stop()

for person in users:
    label = person.get("full_name") or person["email"]
    with st.expander(f"{label} — {person['role']}" + ("" if person["is_active"] else " (disabled)")):
        current = [by_id[d] for d in person.get("department_ids", []) if d in by_id]

        new_role = st.selectbox(
            "Role",
            ["user", "admin"],
            index=0 if person["role"] == "user" else 1,
            key=f"role_{person['id']}",
        )
        new_departments = st.multiselect(
            "Departments", list(by_name), default=current, key=f"dept_{person['id']}"
        )
        active = st.checkbox(
            "Active", value=person["is_active"], key=f"active_{person['id']}"
        )

        left, right = st.columns(2)
        with left:
            if st.button("Save changes", key=f"save_{person['id']}", use_container_width=True):
                try:
                    api.patch(
                        f"/admin/users/{person['id']}",
                        {
                            "role": new_role,
                            "is_active": active,
                            "department_ids": [by_name[n] for n in new_departments],
                        },
                    )
                    st.success("Saved.")
                    st.rerun()
                except api.ApiError as exc:
                    st.error(exc.message)

        with right:
            reset = st.text_input(
                "New password", type="password", key=f"pwd_{person['id']}"
            )
            if st.button("Reset password", key=f"reset_{person['id']}", use_container_width=True):
                if len(reset) < 10:
                    st.error("Password must be at least 10 characters.")
                else:
                    try:
                        api.post(
                            f"/admin/users/{person['id']}/password", {"password": reset}
                        )
                        st.success("Password updated.")
                    except api.ApiError as exc:
                        st.error(exc.message)
