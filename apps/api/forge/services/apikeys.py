"""ApiKeyService - hashed, revocable, per-tenant, role-scoped API keys (finding h).

Unlike the single static FORGE_SERVICE_API_TOKEN (one identity, whole install), these are
per-tenant keys with an assigned role, individually revocable, and carry a `last_used_at`. Only
the SHA-256 hash is stored; the plaintext is returned once at creation and never recoverable.
Presented as `Authorization: Bearer <key>` and resolved in `forge.deps.get_current_user`.
"""

from __future__ import annotations

import hashlib
import secrets as _secrets
from datetime import datetime, timedelta

from sqlalchemy import select

from forge.models.entities import ApiKey
from forge.services.auth import ROLES

_KEY_PREFIX = "forge_sk_"        # recognizable, non-JWT, non-service-token marker
_PREFIX_STORE_LEN = 12           # chars of the plaintext kept (non-secret) for display/lookup
_LAST_USED_THROTTLE_S = 60       # avoid a write on every single request


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def looks_like_api_key(token: str) -> bool:
    return token.startswith(_KEY_PREFIX)


class ApiKeyService:
    @staticmethod
    async def create(
        session, *, tenant_id: str, name: str, role: str = "editor",
        created_by: str | None = None, ttl_days: int | None = None,
    ) -> tuple[ApiKey, str]:
        """Mint a key. Returns (row, plaintext). The plaintext is shown once and NOT stored."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        plaintext = _KEY_PREFIX + _secrets.token_urlsafe(32)
        row = ApiKey(
            tenant_id=tenant_id, name=name, role=role, created_by=created_by,
            prefix=plaintext[:_PREFIX_STORE_LEN], key_hash=_hash(plaintext),
            status="active",
            expires_at=(datetime.utcnow() + timedelta(days=ttl_days)) if ttl_days else None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row, plaintext

    @staticmethod
    async def list(session, tenant_id: str) -> list[ApiKey]:
        rows = await session.execute(
            select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc())
        )
        return list(rows.scalars())

    @staticmethod
    async def revoke(session, *, tenant_id: str, key_id: str) -> bool:
        row = (
            await session.execute(
                select(ApiKey).where(ApiKey.tenant_id == tenant_id, ApiKey.id == key_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.status = "revoked"
        await session.commit()
        return True

    @staticmethod
    async def resolve(session, presented: str) -> ApiKey | None:
        """Return the active, unexpired key matching the presented plaintext, or None. Best-effort
        stamps `last_used_at` (throttled) so key usage is observable without a write per request."""
        if not looks_like_api_key(presented):
            return None
        row = (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == _hash(presented)))
        ).scalar_one_or_none()
        if row is None or row.status != "active":
            return None
        now = datetime.utcnow()
        if row.expires_at is not None and row.expires_at < now:
            return None
        if row.last_used_at is None or (now - row.last_used_at).total_seconds() > _LAST_USED_THROTTLE_S:
            row.last_used_at = now
            try:
                await session.commit()
            except Exception:  # noqa: BLE001 - last_used is telemetry, never fail auth on it
                await session.rollback()
        return row
