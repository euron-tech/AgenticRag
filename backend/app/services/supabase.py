"""Supabase Auth and Storage over the REST API.

Called directly with httpx rather than through supabase-py: the official client
is synchronous, and blocking calls have no place in an async request path.

The service-role key bypasses every policy in the project. It exists only in
this process, only in these functions, and never reaches the frontend.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import Conflict, NotFound, Unauthorized, UpstreamError
from app.core.logging import get_logger

log = get_logger(__name__)

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.supabase_url.rstrip("/"),
            timeout=httpx.Timeout(30.0, read=120.0),
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _anon_headers() -> dict[str, str]:
    return {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type": "application/json",
    }


def _service_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    if extra:
        headers.update(extra)
    return headers


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    for key in ("msg", "message", "error_description", "error"):
        value = body.get(key) if isinstance(body, dict) else None
        if isinstance(value, str):
            return value
    return str(body)[:300]


# --------------------------------------------------------------------- auth
async def sign_in(email: str, password: str) -> dict[str, Any]:
    response = await _http().post(
        "/auth/v1/token",
        params={"grant_type": "password"},
        headers=_anon_headers(),
        json={"email": email, "password": password},
    )
    if response.status_code == 400:
        # Supabase does not distinguish unknown user from wrong password, and
        # neither should we — that difference is an account enumeration oracle.
        raise Unauthorized("Incorrect email or password.")
    if response.status_code >= 400:
        raise UpstreamError(f"Sign-in failed: {_detail(response)}")
    return response.json()


async def create_user(
    email: str, password: str, *, full_name: str | None = None
) -> dict[str, Any]:
    response = await _http().post(
        "/auth/v1/admin/users",
        headers=_service_headers({"Content-Type": "application/json"}),
        json={
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name} if full_name else {},
        },
    )
    if response.status_code in (409, 422):
        raise Conflict(f"An account already exists for {email}.")
    if response.status_code >= 400:
        raise UpstreamError(f"Could not create the account: {_detail(response)}")
    return response.json()


async def set_user_password(user_id: str, password: str) -> None:
    response = await _http().put(
        f"/auth/v1/admin/users/{user_id}",
        headers=_service_headers({"Content-Type": "application/json"}),
        json={"password": password},
    )
    if response.status_code >= 400:
        raise UpstreamError(f"Could not update the password: {_detail(response)}")


async def delete_user(user_id: str) -> None:
    response = await _http().request(
        "DELETE",
        f"/auth/v1/admin/users/{user_id}",
        headers=_service_headers({"Content-Type": "application/json"}),
        json={"should_soft_delete": False},
    )
    if response.status_code == 404:
        return
    if response.status_code >= 400:
        raise UpstreamError(f"Could not delete the account: {_detail(response)}")


# ------------------------------------------------------------------ storage
async def ensure_bucket() -> None:
    """Create the private documents bucket if it is missing. Idempotent."""
    response = await _http().get(
        f"/storage/v1/bucket/{settings.storage_bucket}", headers=_service_headers()
    )
    if response.status_code == 200:
        return
    created = await _http().post(
        "/storage/v1/bucket",
        headers=_service_headers({"Content-Type": "application/json"}),
        json={
            "id": settings.storage_bucket,
            "name": settings.storage_bucket,
            "public": False,
        },
    )
    if created.status_code >= 400 and created.status_code != 409:
        log.warning("bucket_create_failed", extra={"detail": _detail(created)})
    else:
        log.info("bucket_ready", extra={"bucket": settings.storage_bucket})


async def upload(path: str, data: bytes, content_type: str) -> str:
    response = await _http().post(
        f"/storage/v1/object/{settings.storage_bucket}/{path}",
        headers=_service_headers({"Content-Type": content_type, "x-upsert": "true"}),
        content=data,
    )
    if response.status_code >= 400:
        raise UpstreamError(f"Upload to storage failed: {_detail(response)}")
    return path


async def download(path: str) -> bytes:
    response = await _http().get(
        f"/storage/v1/object/{settings.storage_bucket}/{path}",
        headers=_service_headers(),
    )
    if response.status_code == 404:
        raise NotFound("The stored file is missing.")
    if response.status_code >= 400:
        raise UpstreamError(f"Could not read the stored file: {_detail(response)}")
    return response.content


async def remove(path: str) -> None:
    response = await _http().delete(
        f"/storage/v1/object/{settings.storage_bucket}/{path}",
        headers=_service_headers(),
    )
    if response.status_code >= 400 and response.status_code != 404:
        log.warning("storage_delete_failed", extra={"detail": _detail(response)})
