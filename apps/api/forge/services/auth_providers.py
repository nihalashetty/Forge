"""Auth Provider CRUD + a masked /test that shows what would be injected."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.auth_providers.resolver import AuthResolver
from forge.models import AuthProvider
from forge.secrets.store import SecretStore


def _mask(v: Any) -> str:
    s = str(v)
    return ("••••" + s[-4:]) if len(s) > 4 else "••••"


class AuthProviderService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str) -> list[AuthProvider]:
        rows = await session.execute(
            select(AuthProvider).where(AuthProvider.tenant_id == tenant_id, AuthProvider.project_id == project_id)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, ap_id: str) -> AuthProvider | None:
        row = await session.execute(
            select(AuthProvider).where(AuthProvider.tenant_id == tenant_id, AuthProvider.id == ap_id)
        )
        return row.scalar_one_or_none()

    @staticmethod
    async def create(session: AsyncSession, tenant_id: str, project_id: str, *, name: str, kind: str, config: dict, credentials_ref: str | None = None) -> AuthProvider:
        ap = AuthProvider(
            tenant_id=tenant_id, project_id=project_id, name=name, kind=kind,
            config=config or {}, credentials_ref=credentials_ref or (config or {}).get("credentials_ref"),
        )
        session.add(ap)
        await session.commit()
        await session.refresh(ap)
        return ap

    @staticmethod
    async def update(session: AsyncSession, ap: AuthProvider, *, name: str | None = None, kind: str | None = None,
                     config: dict | None = None, credentials_ref: str | None = None) -> AuthProvider:
        if name is not None:
            ap.name = name
        if kind is not None:
            ap.kind = kind
        if config is not None:
            ap.config = config
        if credentials_ref is not None:
            ap.credentials_ref = credentials_ref or None
        await session.commit()
        await session.refresh(ap)
        return ap

    @staticmethod
    async def delete(session: AsyncSession, ap: AuthProvider) -> None:
        await session.delete(ap)
        await session.commit()

    @staticmethod
    async def test(tenant_id: str, project_id: str, provider: AuthProvider, context: dict | None = None) -> dict:
        resolver = AuthResolver(SecretStore())
        try:
            resolved = await resolver.resolve(
                tenant_id=tenant_id, project_id=project_id, provider_id=provider.id or "test",
                context=context or {}, provider=provider, force=True,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "headers": {k: _mask(v) for k, v in resolved.headers.items()},
            "cookies": {k: _mask(v) for k, v in resolved.cookies.items()},
            "params": {k: _mask(v) for k, v in resolved.params.items()},
            "expires_in_seconds": None if resolved.expires_at is None else max(0, int(resolved.expires_at - __import__("time").monotonic())),
        }
