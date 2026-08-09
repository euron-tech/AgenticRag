"""Supabase JWT verification.

Supabase issues HS256 access tokens signed with the project's JWT secret. We
verify signature, expiry, and audience locally — no network call per request.
"""

from __future__ import annotations

import jwt

from app.core.config import settings
from app.core.errors import Unauthorized


def verify_token(token: str) -> dict:
    """Return the token claims, or raise Unauthorized."""
    try:
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Session expired. Please sign in again.") from None
    except jwt.InvalidTokenError:
        # Deliberately vague: never tell a caller which part of the token failed.
        raise Unauthorized("Invalid credentials.") from None

    if not claims.get("sub"):
        raise Unauthorized("Invalid credentials.")
    return claims


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise Unauthorized("Missing Authorization header.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Expected an 'Authorization: Bearer <token>' header.")
    return token.strip()
