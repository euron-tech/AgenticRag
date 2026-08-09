"""Request dependencies: who is calling, and what may they touch."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header

from app.core.errors import Forbidden, Unauthorized
from app.core.logging import set_request_context
from app.core.security import bearer_token, verify_token
from app.db import pool


@dataclass
class CurrentUser:
    id: str
    email: str
    role: str
    full_name: str | None = None
    department_ids: list[str] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def may_access(self, department_id: str) -> bool:
        return self.is_admin or str(department_id) in self.department_ids


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    claims = verify_token(bearer_token(authorization))
    user_id = str(claims["sub"])

    # Read the profile through RLS. If the row is invisible or missing, the
    # account was deleted or deactivated and the token should stop working.
    async with pool.rls_conn(user_id) as conn:
        profile = await conn.fetchrow(
            "select id, email, full_name, role, is_active from profiles where id = $1",
            uuid.UUID(user_id),
        )
        if profile is None:
            raise Unauthorized("Account not found.")
        if not profile["is_active"]:
            raise Forbidden("This account has been deactivated.")
        departments = await conn.fetch(
            "select department_id from user_departments where user_id = $1",
            uuid.UUID(user_id),
        )

    set_request_context(user_id=user_id)
    return CurrentUser(
        id=user_id,
        email=profile["email"],
        role=profile["role"],
        full_name=profile["full_name"],
        department_ids=[str(r["department_id"]) for r in departments],
    )


async def require_admin(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    if not user.is_admin:
        raise Forbidden("Administrator access is required for this action.")
    return user


def require_department(user: CurrentUser, department_id: str) -> None:
    """Second layer behind RLS. Gives a clear 403 instead of an empty result."""
    if not user.may_access(department_id):
        raise Forbidden("You do not have access to this department.")


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]
