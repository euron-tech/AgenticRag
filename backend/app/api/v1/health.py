"""Health endpoints.

`/health` is shallow and is what the ALB target group polls — it must not fail
because a dependency is briefly slow, or a blue/green cutover will thrash.
`/health/ready` is deep and is what the deployment smoke test calls.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.core.config import settings
from app.db import pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "env": settings.app_env}


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, object]:
    database = await pool.healthcheck()
    if not database:
        response.status_code = 503
    return {
        "status": "ok" if database else "degraded",
        "checks": {"database": database},
        "env": settings.app_env,
    }
