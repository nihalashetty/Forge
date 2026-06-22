"""Auth primitives: password hashing (bcrypt) + JWT mint/verify.

Kept dependency-light and side-effect-free so it's unit-testable without a DB. The
actual user lookup / login flow lives in `services/auth.py`; this module only knows
how to hash a password and sign/verify a token.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from forge.config import settings


class TokenError(ValueError):
    """Raised when a JWT is missing, malformed, expired, revoked, or has the wrong type."""


# In-process revocation denylist: jti -> expiry (epoch seconds). Identity-bearing tokens
# (session tokens for the embed widget) can't otherwise be cancelled before their TTL, so
# `revoke()` lets an operator kill a leaked one (audit S11). In-process only - back with
# Redis for multi-worker (same interface). Entries self-expire at the token's own exp.
_REVOKED: dict[str, float] = {}


def revoke(jti: str, *, exp: float | None = None) -> None:
    """Revoke a token by its `jti`. `exp` (the token's expiry) lets the entry self-prune."""
    if not jti:
        return
    now = time.time()
    # opportunistic prune of expired entries
    for k, v in list(_REVOKED.items()):
        if v and v < now:
            _REVOKED.pop(k, None)
    _REVOKED[jti] = exp or (now + 86400)


def is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    exp = _REVOKED.get(jti)
    if exp is None:
        return False
    if exp and exp < time.time():
        _REVOKED.pop(jti, None)
        return False
    return True


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
    payload = {**claims, "iat": now, "exp": now + ttl, "type": token_type, "jti": uuid.uuid4().hex}
    # `kid` header identifies the signing key so verifiers can support rotation.
    return jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm,
        headers={"kid": settings.jwt_key_id},
    )


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


def create_session_token(*, tenant_id: str, project_id: str, end_user: dict, origins: list[str] | None = None, ttl_minutes: int = 30) -> str:
    """Short-lived signed token carrying a VERIFIED end-user identity for the browser widget
    (Phase 3b). Minted server-to-server by the integrator's authenticated backend; the widget
    sends it on each run so Forge trusts the identity without trusting the browser. The secret
    stays server-side - only this scoped, expiring token reaches the client."""
    return _encode(
        {"tid": tenant_id, "pid": project_id, "end_user": end_user, "origins": origins or []},
        timedelta(minutes=ttl_minutes),
        "session",
    )


def decode_token(token: str, *, expected_type: str | None = None) -> dict:
    # Accept the current signing key plus any configured previous keys (rotation overlap).
    keys = [settings.jwt_secret, *(settings.jwt_secret_previous or [])]
    last_err: Exception | None = None
    claims: dict | None = None
    for key in keys:
        try:
            claims = jwt.decode(token, key, algorithms=[settings.jwt_algorithm])
            break
        except JWTError as e:
            last_err = e
    if claims is None:
        raise TokenError(f"invalid token: {last_err}")
    if expected_type and claims.get("type") != expected_type:
        raise TokenError(f"expected a {expected_type} token, got {claims.get('type')!r}")
    if is_revoked(claims.get("jti")):
        raise TokenError("token has been revoked")
    return claims
