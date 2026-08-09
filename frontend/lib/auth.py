"""Sign-in screen and session helpers.

Hiding an admin page here is a convenience, not a control. Every admin action
is enforced by the backend against the JWT, so a user who guesses a URL still
gets a 403.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from lib import api


def is_signed_in() -> bool:
    return bool(st.session_state.get("access_token") and st.session_state.get("profile"))


def profile() -> dict[str, Any]:
    return st.session_state.get("profile") or {}


def is_admin() -> bool:
    return profile().get("role") == "admin"


def sign_out() -> None:
    st.session_state.clear()
    st.rerun()


def sign_in_screen() -> None:
    st.markdown("## Company Document Assistant")
    st.caption("Sign in with the credentials your administrator issued.")

    with st.form("sign_in", clear_on_submit=False):
        email = st.text_input("Email", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

    if not submitted:
        return

    if not email or not password:
        st.error("Enter both your email and password.")
        return

    try:
        session = api.login(email.strip(), password)
    except api.ApiError as exc:
        st.error(exc.message)
        return

    st.session_state["access_token"] = session["access_token"]
    st.session_state["profile"] = session["profile"]
    st.rerun()


def sidebar_account() -> None:
    person = profile()
    with st.sidebar:
        st.markdown(f"**{person.get('full_name') or person.get('email', '')}**")
        st.caption("Administrator" if is_admin() else "Member")
        if st.button("Sign out", use_container_width=True):
            sign_out()


def department_picker(key: str = "department_id") -> dict[str, Any] | None:
    """Department is always an explicit choice — never inferred."""
    try:
        departments = api.my_departments()
    except api.ApiError as exc:
        st.error(exc.message)
        return None

    if not departments:
        st.warning(
            "You do not have access to any department yet. "
            "Ask an administrator to grant you access."
        )
        return None

    names = {d["name"]: d for d in departments}
    chosen = st.sidebar.selectbox("Department", list(names), key=key)
    return names[chosen]
