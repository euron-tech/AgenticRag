"""Sign-in and identity."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUserDep
from app.core.errors import Forbidden
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.pool import rls_conn
from app.schemas import LoginRequest, LoginResponse, Profile
from app.services import supabase

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> LoginResponse:
    session = await supabase.sign_in(payload.email, payload.password)
    user = session.get("user") or {}
    user_id = str(user.get("id"))

    async with rls_conn(user_id) as conn:
        row = await conn.fetchrow(
            "select id, email, full_name, role, is_active from profiles where id = $1::uuid",
            user_id,
        )
        if row is None:
            raise Forbidden(
                "This account has no profile yet. Ask an administrator to finish setting it up."
            )
        if not row["is_active"]:
            raise Forbidden("This account has been deactivated.")
        departments = await conn.fetch(
            "select department_id from user_departments where user_id = $1::uuid", user_id
        )

    log.info("login_success", extra={"role": row["role"]})
    return LoginResponse(
        access_token=session["access_token"],
        expires_at=session.get("expires_at"),
        profile=Profile(
            id=user_id,
            email=row["email"],
            full_name=row["full_name"],
            role=row["role"],
            is_active=row["is_active"],
            department_ids=[str(r["department_id"]) for r in departments],
        ),
    )


@router.get("/me", response_model=Profile)
async def me(user: CurrentUserDep) -> Profile:
    return Profile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,  # type: ignore[arg-type]
        is_active=True,
        department_ids=user.department_ids,
    )


@router.get("/departments")
async def my_departments(user: CurrentUserDep) -> list[dict]:
    async with rls_conn(user.id) as conn:
        rows = await repo.list_departments(conn)
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "slug": r["slug"],
            "description": r["description"],
            "document_count": r["document_count"],
        }
        for r in rows
    ]
