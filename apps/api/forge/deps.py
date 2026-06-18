"""FastAPI dependencies: session, auth/tenant resolution, RBAC.

Auth rollout is gated by `settings.auth_required`:
- True  → every request must carry a valid `Authorization: Bearer <access-token>`;
          the user is loaded from the DB and must be `active`.
- False → requests with no token fall back to the seeded workspace owner so the
          console keeps working during the migration (dev default).

`current_tenant_id` is derived from the resolved user, so every existing route that
already depends on it becomes tenant-scoped automatically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import settings
from forge.db import SessionLocal
from forge.security import TokenError, decode_token
from forge.services.auth import AuthService, role_at_least
from forge.services.runs import RunService


@dataclass
class CurrentUser:
    id: str
    tenant_id: str
    role: str
    email: str | None = None
    is_fallback: bool = False


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


async def get_current_user(request: Request) -> CurrentUser:
    token = _bearer(request)
    if token:
        try:
            claims = decode_token(token, expected_type="access")
        except TokenError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e
        async with SessionLocal() as s:
            user = await AuthService.get_user(s, claims.get("sub", ""))
        if user is None or user.status != "active":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found or disabled")
        return CurrentUser(id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email)

    if settings.auth_required:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")

    # Backward-compatible fallback: the seeded workspace owner.
    tenant_id = getattr(request.app.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no authenticated user")
    return CurrentUser(id="system-dev", tenant_id=tenant_id, role="owner", email="you@forge.local", is_fallback=True)


def current_tenant_id(user: CurrentUser = Depends(get_current_user)) -> str:
    return user.tenant_id


def require_role(minimum: str):
    """Dependency factory: require the caller's role to be at least `minimum`
    (owner > admin > editor > viewer). Use on mutating/administrative routes."""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not role_at_least(user.role, minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role '{minimum}' or higher")
        return user

    return _dep


def _trusts_peer(peer: str | None) -> bool:
    """Whether to believe an X-Forwarded-For from this socket peer. Only configured
    reverse-proxy IPs are trusted, so an arbitrary client can't spoof its IP for per-IP
    rate limits / audit (audit L2)."""
    tp = settings.trusted_proxies
    if not tp:
        return False
    if "*" in tp:
        return True
    return peer in tp


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    fwd = request.headers.get("x-forwarded-for")
    if fwd and _trusts_peer(peer):
        # Left-most entry is the original client (proxies append on the right).
        return fwd.split(",")[0].strip()
    return peer


def get_run_service(request: Request) -> RunService:
    return RunService(
        checkpointer=getattr(request.app.state, "checkpointer", None),
        store=getattr(request.app.state, "store", None),
    )


def get_checkpointer(request: Request):
    return getattr(request.app.state, "checkpointer", None)
