"""Streamlit entrypoint.

One app, two surfaces. The navigation is built from the signed-in user's role,
so a member never sees the admin pages — and the backend refuses them anyway.
"""

from __future__ import annotations

import streamlit as st

from lib import auth

st.set_page_config(
    page_title="Company Document Assistant",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

if not auth.is_signed_in():
    _, middle, _ = st.columns([1, 1.4, 1])
    with middle:
        auth.sign_in_screen()
    st.stop()

auth.sidebar_account()

chat_pages = [
    st.Page("views/chat.py", title="Chat", icon="💬", default=True),
    st.Page("views/history.py", title="History", icon="🕘"),
]

admin_pages = [
    st.Page("views/admin_documents.py", title="Documents", icon="📁"),
    st.Page("views/admin_departments.py", title="Departments", icon="🏢"),
    st.Page("views/admin_users.py", title="Users", icon="👤"),
    st.Page("views/admin_audit.py", title="Audit log", icon="📋"),
]

sections = {"Assistant": chat_pages}
if auth.is_admin():
    sections["Administration"] = admin_pages

st.navigation(sections).run()
