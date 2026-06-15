"""Auth primitives: password hashing (bcrypt) + JWT mint/verify.

Kept dependency-light and side-effect-free so it's unit-testable without a DB. The
actual user lookup / login flow lives in `services/auth.py`; this module only knows
how to hash a password and sign/verify a token.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from forge.config import settings


class TokenError(ValueError):
    """Raised when a JWT is missing, malformed, expired, or has the wrong type."""


def _pw_bytes(password: str) -> bytes:
    # bcrypt hard-caps the password at 72 bytes (and bcrypt>=4.1 raises rather than
    # truncating), so slice the encoded bytes explicitly.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_pw_bytes(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _encode(claims: dict, ttl: timedelta, token_type: str) -> str:
    now = datetime.now(UTC)
    payload = {**claims, "iat": now, "exp": now + ttl, "type": token_type}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(*, user_id: str, tenant_id: str, role: str, ttl_minutes: int | None = None) -> str:
    ttl = timedelta(minutes=ttl_minutes or settings.access_token_ttl_minutes)
    return _encode({"sub": user_id, "tid": tenant_id, "role": role}, ttl, "access")


def create_refresh_token(*, user_id: str, tenant_id: str) -> str:
    ttl = timedelta(days=settings.refresh_token_ttl_days)
    return _encode({"sub": user_id, "tid": tenant_id}, ttl, "refresh")


def create_state_token(payload: dict, *, ttl_minutes: int = 10) -> str:
    """Short-lived signed state for the OAuth connect flow (stateless CSRF protection)."""
    return _encode(payload, timedelta(minutes=ttl_minutes), "oauth_state")


def create_invite_token(*, user_id: str, tenant_id: str, ttl_days: int = 7) -> str:
    """Signed token emailed to an invited teammate; redeemed to set their password."""
    return _encode({"sub": user_id, "tid": tenant_id}, timedelta(days=ttl_days), "invite")


def decode_token(token: str, *, expected_type: str | None = None) -> dict:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise TokenError(f"invalid token: {e}") from e
    if expected_type and claims.get("type") != expected_type:
        raise TokenError(f"expected a {expected_type} token, got {claims.get('type')!r}")
    return claims
