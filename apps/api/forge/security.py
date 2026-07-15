"""Auth primitives: password hashing (bcrypt) + JWT mint/verify.

Kept dependency-light and side-effect-free so it's unit-testable without a DB. The
actual user lookup / login flow lives in `services/auth.py`; this module only knows
how to hash a password and sign/verify a token.
"""

from __future__ import annotations

import hmac
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from forge.config import settings


class TokenError(ValueError):
    """Raised when a JWT is missing, malformed, expired, revoked, or has the wrong type."""


class _Revocations:
    """Token revocation state: a per-`jti` denylist plus per-user logout-all cutoffs.

    Identity-bearing tokens (embed session tokens; rotated refresh tokens) can't otherwise be
    cancelled before their TTL, so revoking lets an operator kill a leaked one / rotate on use
    (audit S11, finding d). In-process by default; persisted in REDIS when FORGE_REDIS_URL is
    set so revocations are shared across workers and survive a restart. Redis is (re)connected
    lazily; on any Redis error we fall back to the in-process maps for that call. Entries
    self-expire at the token's own exp.
    """

    _RETRY = 15.0

    def __init__(self) -> None:
        self._jti: dict[str, float] = {}        # jti -> expiry (epoch seconds)
        self._user_cut: dict[str, float] = {}   # user_id -> "tokens issued before this are dead"
        self._client = None
        self._last_attempt = 0.0
        self._lock = threading.Lock()

    def _redis(self):
        url = settings.redis_url
        if not url or self._client is not None:
            return self._client
        now = time.time()
        with self._lock:
            if self._client is not None:
                return self._client
            if now - self._last_attempt < self._RETRY:
                return None
            self._last_attempt = now
            try:
                import redis

                c = redis.Redis.from_url(url, decode_responses=True)
                c.ping()
                self._client = c
            except Exception:  # noqa: BLE001 - keep in-process, retry later
                pass
        return self._client

    def revoke_jti(self, jti: str, *, exp: float | None = None) -> None:
        if not jti:
            return
        now = time.time()
        r = self._redis()
        if r is not None:
            try:
                ttl = max(1, int((exp or now + 86400) - now))
                r.set(f"forge:revk:jti:{jti}", "1", ex=ttl)
                return
            except Exception:  # noqa: BLE001 - fall through to in-process
                pass
        for k, v in list(self._jti.items()):  # opportunistic prune
            if v and v < now:
                self._jti.pop(k, None)
        self._jti[jti] = exp or (now + 86400)

    def is_jti_revoked(self, jti: str | None) -> bool:
        if not jti:
            return False
        r = self._redis()
        if r is not None:
            try:
                return bool(r.exists(f"forge:revk:jti:{jti}"))
            except Exception:  # noqa: BLE001
                pass
        exp = self._jti.get(jti)
        if exp is None:
            return False
        if exp and exp < time.time():
            self._jti.pop(jti, None)
            return False
        return True

    def revoke_user_since(self, user_id: str, ts: float) -> None:
        """Invalidate every token issued to `user_id` before `ts` (logout-all / password change)."""
        if not user_id:
            return
        r = self._redis()
        if r is not None:
            try:
                r.set(f"forge:revk:user:{user_id}", str(int(ts)))
                return
            except Exception:  # noqa: BLE001
                pass
        self._user_cut[user_id] = ts

    def user_revoked_since(self, user_id: str | None) -> float | None:
        if not user_id:
            return None
        r = self._redis()
        if r is not None:
            try:
                v = r.get(f"forge:revk:user:{user_id}")
                return float(v) if v else None
            except Exception:  # noqa: BLE001
                pass
        return self._user_cut.get(user_id)


_revocations = _Revocations()


def revoke(jti: str, *, exp: float | None = None) -> None:
    """Revoke a single token by its `jti`. `exp` (the token's expiry) lets the entry self-prune."""
    _revocations.revoke_jti(jti, exp=exp)


def is_revoked(jti: str | None) -> bool:
    return _revocations.is_jti_revoked(jti)


def revoke_user_tokens(user_id: str, *, since: float | None = None) -> None:
    """Logout-all: kill every token issued to `user_id` before `since` (default now)."""
    _revocations.revoke_user_since(user_id, since if since is not None else time.time())


def tokens_revoked_after(claims: dict) -> bool:
    """True if the caller's token predates a logout-all / password-change cutoff for its user."""
    cut = _revocations.user_revoked_since(claims.get("sub"))
    if cut is None:
        return False
    iat = claims.get("iat")
    try:
        return iat is None or float(iat) < float(cut)
    except (TypeError, ValueError):
        return True


# --- TOTP (RFC 6238) - stdlib only, no external dependency (finding j) --------------------


def generate_totp_secret() -> str:
    """A fresh base32 TOTP shared secret (160-bit)."""
    import base64
    import os

    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _totp_at(secret: str, counter: int, digits: int = 6) -> str:
    import base64
    import hashlib
    import hmac
    import struct

    pad = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret.upper() + pad, casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp(secret: str | None, code: str | None, *, window: int = 1) -> bool:
    """Constant-ish TOTP check tolerating +/- `window` 30s steps for clock drift."""
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    step = int(time.time() // 30)
    return any(hmac.compare_digest(_totp_at(secret, step + off), code) for off in range(-window, window + 1))


def totp_provisioning_uri(secret: str, *, account: str, issuer: str = "Forge") -> str:
    """otpauth:// URI for an authenticator-app QR code."""
    from urllib.parse import quote, urlencode

    label = quote(f"{issuer}:{account}")
    q = urlencode({"secret": secret, "issuer": issuer, "period": 30, "digits": 6})
    return f"otpauth://totp/{label}?{q}"


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


def create_password_reset_token(*, user_id: str, tenant_id: str, ttl_minutes: int = 60) -> str:
    """Short-lived signed token emailed for a self-service password reset (finding j). Reuses
    the invite-token machinery; short TTL bounds the window a leaked link is usable."""
    return _encode({"sub": user_id, "tid": tenant_id}, timedelta(minutes=ttl_minutes), "pwreset")


def create_email_verification_token(*, user_id: str, tenant_id: str, ttl_days: int = 3) -> str:
    """Signed token emailed to confirm ownership of a sign-up address (finding j)."""
    return _encode({"sub": user_id, "tid": tenant_id}, timedelta(days=ttl_days), "email_verify")


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


def create_mcp_authorization_code(*, claims: dict, ttl_minutes: int = 5) -> str:
    """Short-lived OAuth 2.1 authorization code (MCP auth server). Carries the bound client_id,
    redirect_uri, PKCE challenge, requested resource, and the authenticated user - so /token can
    verify the exchange statelessly. Never returned to the model; only to the redirect_uri."""
    return _encode(claims, timedelta(minutes=ttl_minutes), "mcp_auth_code")


def create_mcp_access_token(*, claims: dict, ttl_minutes: int = 60) -> str:
    """OAuth 2.1 access token for a Forge MCP resource. `aud` is bound to the project's canonical
    MCP URL (RFC 8707); the MCP resource server validates the audience before honoring it."""
    return _encode(claims, timedelta(minutes=ttl_minutes), "mcp_access")


def create_mcp_refresh_token(*, claims: dict, ttl_days: int = 30) -> str:
    return _encode(claims, timedelta(days=ttl_days), "mcp_refresh")


def decode_token(token: str, *, expected_type: str | None = None, check_revoked: bool = True) -> dict:
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
    # `check_revoked=False` lets the refresh flow inspect a still-signature-valid token whose jti
    # is already denylisted (a REUSE of a rotated refresh token) so it can escalate to a
    # logout-all instead of a bare 401 (finding d).
    if check_revoked and is_revoked(claims.get("jti")):
        raise TokenError("token has been revoked")
    return claims
