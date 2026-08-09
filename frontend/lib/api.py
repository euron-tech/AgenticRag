"""HTTP client for the backend API.

Streamlit runs synchronously, so this is a sync client. Every call carries the
signed-in user's JWT; the backend re-derives identity and department access
from that token, never from anything this process claims.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
API = f"{BACKEND_URL}/api/v1"

DEFAULT_TIMEOUT = httpx.Timeout(30.0, read=180.0)


class ApiError(Exception):
    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _token() -> str | None:
    return st.session_state.get("access_token")


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {}
    if token := _token():
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


def _handle(response: httpx.Response) -> Any:
    if response.status_code == 401:
        # The token is gone or expired — drop the session rather than showing a
        # logged-in shell that fails every action.
        st.session_state.clear()
        raise ApiError("Your session has expired. Please sign in again.", 401)
    if response.status_code >= 400:
        message = f"Request failed ({response.status_code})."
        try:
            body = response.json()
            if isinstance(body, dict):
                message = body.get("error", {}).get("message") or body.get("detail") or message
        except ValueError:
            pass
        raise ApiError(str(message), response.status_code)
    if not response.content:
        return None
    return response.json()


def _request(method: str, path: str, **kwargs: Any) -> Any:
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.request(method, f"{API}{path}", **kwargs)
    except httpx.RequestError as exc:
        raise ApiError(f"Cannot reach the API at {BACKEND_URL}: {exc}") from exc
    return _handle(response)


def get(path: str, params: dict[str, Any] | None = None) -> Any:
    return _request("GET", path, headers=_headers(), params=params)


def post(path: str, payload: dict[str, Any] | None = None) -> Any:
    return _request("POST", path, headers=_headers(), json=payload or {})


def patch(path: str, payload: dict[str, Any]) -> Any:
    return _request("PATCH", path, headers=_headers(), json=payload)


def delete(path: str) -> Any:
    return _request("DELETE", path, headers=_headers())


def upload(path: str, *, department_id: str, filename: str, content: bytes) -> Any:
    return _request(
        "POST",
        path,
        headers=_headers(),
        data={"department_id": department_id},
        files={"file": (filename, content)},
    )


# ------------------------------------------------------------- convenience
def login(email: str, password: str) -> dict[str, Any]:
    return _request("POST", "/auth/login", json={"email": email, "password": password})


def my_departments() -> list[dict[str, Any]]:
    return get("/auth/departments") or []


def documents(department_id: str) -> list[dict[str, Any]]:
    return get("/documents", {"department_id": department_id}) or []


def conversations(department_id: str | None = None) -> list[dict[str, Any]]:
    params = {"department_id": department_id} if department_id else None
    return get("/conversations", params) or []


def messages(conversation_id: str) -> list[dict[str, Any]]:
    return get(f"/conversations/{conversation_id}/messages") or []


def ask(department_id: str, message: str, conversation_id: str | None) -> dict[str, Any]:
    return post(
        "/chat",
        {
            "department_id": department_id,
            "message": message,
            "conversation_id": conversation_id,
        },
    )
