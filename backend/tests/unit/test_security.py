"""Token verification, for both signing modes Supabase uses.

Current Supabase projects sign access tokens with ES256 and publish the public
key at a JWKS endpoint; older projects use an HS256 shared secret. Both must
work, and neither may leak which part of a bad token failed.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import security
from app.core.errors import Unauthorized

SECRET = "test-jwt-secret-not-a-real-one"


@pytest.fixture(scope="module")
def ec_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def jwks(monkeypatch, ec_key):
    """Serve the public half locally instead of fetching a JWKS endpoint."""

    class FakeKey:
        key = ec_key.public_key()

    class FakeClient:
        def get_signing_key_from_jwt(self, _token):
            return FakeKey()

    monkeypatch.setattr(security, "_jwks", lambda: FakeClient())


def claims(**overrides) -> dict:
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------- ES256
def test_asymmetric_token_is_accepted(jwks, ec_key):
    token = jwt.encode(claims(), ec_key, algorithm="ES256")
    assert security.verify_token(token)["sub"].startswith("1111")


def test_expired_asymmetric_token_says_so(jwks, ec_key):
    token = jwt.encode(claims(exp=int(time.time()) - 10), ec_key, algorithm="ES256")
    with pytest.raises(Unauthorized) as exc:
        security.verify_token(token)
    assert "expired" in str(exc.value).lower()


def test_token_signed_by_a_different_key_is_rejected(jwks):
    other = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(claims(), other, algorithm="ES256")
    with pytest.raises(Unauthorized):
        security.verify_token(token)


def test_wrong_audience_is_rejected(jwks, ec_key):
    token = jwt.encode(claims(aud="anon"), ec_key, algorithm="ES256")
    with pytest.raises(Unauthorized):
        security.verify_token(token)


# ------------------------------------------------------------------- HS256
def test_legacy_symmetric_token_is_accepted(monkeypatch):
    monkeypatch.setattr(security.settings, "supabase_jwt_secret", SECRET)
    token = jwt.encode(claims(), SECRET, algorithm="HS256")
    assert security.verify_token(token)["sub"].startswith("1111")


def test_legacy_token_without_a_configured_secret_is_rejected(monkeypatch):
    monkeypatch.setattr(security.settings, "supabase_jwt_secret", "")
    token = jwt.encode(claims(), SECRET, algorithm="HS256")
    with pytest.raises(Unauthorized):
        security.verify_token(token)


# ----------------------------------------------------------------- general
def test_unsigned_tokens_are_rejected():
    """`alg: none` is the oldest JWT attack there is."""
    token = jwt.encode(claims(), key="", algorithm="none")
    with pytest.raises(Unauthorized):
        security.verify_token(token)


def test_garbage_is_rejected():
    with pytest.raises(Unauthorized):
        security.verify_token("not-a-token")


def test_failure_messages_do_not_reveal_which_check_failed(jwks):
    """A precise error is an oracle for forging tokens."""
    other = ec.generate_private_key(ec.SECP256R1())
    message = str(
        pytest.raises(
            Unauthorized,
            security.verify_token,
            jwt.encode(claims(), other, algorithm="ES256"),
        ).value
    )
    assert message == "Invalid credentials."


def test_bearer_header_parsing():
    assert security.bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
    assert security.bearer_token("bearer abc") == "abc"
    for bad in (None, "", "Basic abc", "Bearer", "abc"):
        with pytest.raises(Unauthorized):
            security.bearer_token(bad)
