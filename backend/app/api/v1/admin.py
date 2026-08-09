"""Admin console: accounts, departments, audit.

Account creation runs on the service-role key, which bypasses every policy in
the project. That key never leaves this process.
"""

from __future__ import annotations

import re

from fastapi import APIRouter

from app.core.deps import AdminDep
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.pool import admin_conn
from app.schemas import (
    AuditEntry,
    Department,
    DepartmentCreate,
    Ok,
    Profile,
    UserCreate,
    UserUpdate,
)
from app.services import supabase

log = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "department"


# ------------------------------------------------------------- departments
@router.get("/departments", response_model=list[Department])
async def list_departments(_: AdminDep) -> list[Department]:
    async with admin_conn() as conn:
        rows = await repo.list_departments(conn)
    return [
        Department(
            id=str(r["id"]),
            name=r["name"],
            slug=r["slug"],
            description=r["description"],
            is_active=r["is_active"],
            document_count=r["document_count"],
        )
        for r in rows
    ]


@router.post("/departments", response_model=Department, status_code=201)
async def create_department(payload: DepartmentCreate, admin: AdminDep) -> Department:
    slug = _slugify(payload.name)
    async with admin_conn() as conn:
        existing = await conn.fetchval("select 1 from departments where slug = $1", slug)
        if existing:
            raise Conflict(f"A department with the slug '{slug}' already exists.")
        row = await repo.create_department(
            conn, name=payload.name, slug=slug, description=payload.description
        )
        await repo.write_audit(
            conn,
            actor_id=admin.id,
            action="department.create",
            entity_type="department",
            entity_id=str(row["id"]),
            payload={"name": payload.name, "slug": slug},
        )
    log.info("department_created", extra={"slug": slug})
    return Department(
        id=str(row["id"]),
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        is_active=row["is_active"],
    )


# ------------------------------------------------------------------- users
@router.get("/users", response_model=list[Profile])
async def list_users(_: AdminDep) -> list[Profile]:
    async with admin_conn() as conn:
        rows = await repo.list_profiles(conn)
    return [
        Profile(
            id=str(r["id"]),
            email=r["email"],
            full_name=r["full_name"],
            role=r["role"],
            is_active=r["is_active"],
            department_ids=[str(d) for d in (r["department_ids"] or [])],
        )
        for r in rows
    ]


@router.post("/users", response_model=Profile, status_code=201)
async def create_user(payload: UserCreate, admin: AdminDep) -> Profile:
    created = await supabase.create_user(
        payload.email, payload.password, full_name=payload.full_name
    )
    user_id = str(created.get("id"))

    try:
        async with admin_conn() as conn:
            async with conn.transaction():
                await repo.upsert_profile(
                    conn,
                    user_id=user_id,
                    email=payload.email,
                    full_name=payload.full_name,
                    role=payload.role,
                )
                await repo.set_user_departments(
                    conn,
                    user_id=user_id,
                    department_ids=payload.department_ids,
                    granted_by=admin.id,
                )
                await repo.write_audit(
                    conn,
                    actor_id=admin.id,
                    action="user.create",
                    entity_type="user",
                    entity_id=user_id,
                    payload={"email": payload.email, "role": payload.role,
                             "departments": payload.department_ids},
                )
    except Exception:
        # Do not leave an auth account with no profile — it would be able to
        # sign in and then fail every request with a confusing error.
        log.exception("profile_creation_failed_rolling_back_auth_user")
        await supabase.delete_user(user_id)
        raise

    log.info("user_created", extra={"role": payload.role})
    return Profile(
        id=user_id,
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        is_active=True,
        department_ids=payload.department_ids,
    )


@router.patch("/users/{user_id}", response_model=Ok)
async def update_user(user_id: str, payload: UserUpdate, admin: AdminDep) -> Ok:
    async with admin_conn() as conn:
        exists = await conn.fetchval("select 1 from profiles where id = $1::uuid", user_id)
        if not exists:
            raise NotFound("User not found.")
        async with conn.transaction():
            await repo.update_profile(
                conn,
                user_id=user_id,
                full_name=payload.full_name,
                role=payload.role,
                is_active=payload.is_active,
            )
            if payload.department_ids is not None:
                await repo.set_user_departments(
                    conn,
                    user_id=user_id,
                    department_ids=payload.department_ids,
                    granted_by=admin.id,
                )
            await repo.write_audit(
                conn,
                actor_id=admin.id,
                action="user.update",
                entity_type="user",
                entity_id=user_id,
                payload=payload.model_dump(exclude_none=True),
            )
    return Ok()


@router.post("/users/{user_id}/password", response_model=Ok)
async def reset_password(user_id: str, payload: dict, admin: AdminDep) -> Ok:
    password = str(payload.get("password", ""))
    if len(password) < 10:
        raise Conflict("Password must be at least 10 characters.")
    await supabase.set_user_password(user_id, password)
    async with admin_conn() as conn:
        await repo.write_audit(
            conn,
            actor_id=admin.id,
            action="user.password_reset",
            entity_type="user",
            entity_id=user_id,
        )
    log.info("password_reset", extra={"target_user": user_id})
    return Ok()


# ------------------------------------------------------------------- audit
@router.get("/audit", response_model=list[AuditEntry])
async def audit(_: AdminDep, limit: int = 200) -> list[AuditEntry]:
    async with admin_conn() as conn:
        rows = await repo.list_audit(conn, limit=min(limit, 500))
    return [
        AuditEntry(
            id=str(r["id"]),
            actor_email=r["actor_email"],
            action=r["action"],
            entity_type=r["entity_type"],
            entity_id=str(r["entity_id"]) if r["entity_id"] else None,
            payload=r["payload"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
