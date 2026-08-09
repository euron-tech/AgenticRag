"""Supabase JWT verification.

Supabase issues access tokens two different ways depending on the project:

* **Asymmetric (current default)** — ES256/RS256, signed with a rotating key
  whose public half is published at `/auth/v1/.well-known/jwks.json`. Verified
  against that endpoint; the key set is cached, so this is not a per-request
  network call.
* **Legacy symmetric** — HS256, signed with the project's shared JWT secret.

Which one is in use is read from the token's own header rather than configured,
so a project that rotates to asymmetric keys keeps working without a redeploy.
"""

from __future__ import annotations

import jwt
from jwt import PyJWKClient

from app.core.config import settings
from app.core.errors import Unauthorized
from app.core.logging import get_logger

log = get_logger(__name__)

ASYMMETRIC = ("ES256", "ES384", "RS256", "RS384", "RS512")

_jwks_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        # lifespan=600: keys are cached for ten minutes, so a rotation is picked
        # up without a restart and without a fetch on every request.
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=600)
    return _jwks_client


def verify_token(token: str) -> dict:
    """Return the token claims, or raise Unauthorized."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        raise Unauthorized("Invalid credentials.") from None

    algorithm = header.get("alg", "")
    options = {"require": ["exp", "sub"]}

    try:
        if algorithm in ASYMMETRIC:
            key = _jwks().get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=list(ASYMMETRIC),
                audience="authenticated",
                options=options,
            )
        elif algorithm == "HS256":
            if not settings.supabase_jwt_secret:
                raise Unauthorized(
                    "This token is signed with the legacy shared secret, but "
                    "SUPABASE_JWT_SECRET is not configured."
                )
            claims = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
                options=options,
            )
        else:
            log.warning("unsupported_token_algorithm", extra={"alg": algorithm})
            raise Unauthorized("Invalid credentials.")
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Session expired. Please sign in again.") from None
    except jwt.InvalidTokenError:
        # Deliberately vague: never tell a caller which part of the token failed.
        raise Unauthorized("Invalid credentials.") from None
    except Unauthorized:
        raise
    except Exception as exc:
        # A JWKS fetch failure is an availability problem, not a bad credential.
        log.warning("token_verification_error", extra={"error_type": type(exc).__name__})
        raise Unauthorized("Could not verify credentials.") from None

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
