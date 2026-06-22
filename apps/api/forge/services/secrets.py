"""Secret write/list (write-only API; values never returned)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models import AuthProvider, Project, Secret, Tool, Trigger
from forge.secrets.store import SecretStore

# Matches secret://… and vault://… refs anywhere in a string; the secret name is the last segment.
_SECRET_REF_RE = re.compile(r"(?:secret|vault)://[^\s\"']+")


def _referenced_names(obj: Any) -> set[str]:
    """Every secret name referenced anywhere in a JSON-ish structure (or a plain string column)."""
    if isinstance(obj, str):
        return {ref.rsplit("/", 1)[-1] for ref in _SECRET_REF_RE.findall(obj)}
    if isinstance(obj, dict):
        out: set[str] = set()
        for v in obj.values():
            out |= _referenced_names(v)
        return out
    if isinstance(obj, (list, tuple)):
        out = set()
        for v in obj:
            out |= _referenced_names(v)
        return out
    return set()


class SecretService:
    store = SecretStore()

    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str) -> list[Secret]:
        rows = await session.execute(
            select(Secret).where(Secret.tenant_id == tenant_id, Secret.project_id == project_id)
        )
        return list(rows.scalars())

    @classmethod
    async def write(
        cls, session: AsyncSession, tenant_id: str, project_id: str, *, name: str, value: Any, kind: str = "generic"
    ) -> Secret:
        return await cls.store.write(
            session, tenant_id=tenant_id, project_id=project_id, name=name, value=value, kind=kind
        )

    @staticmethod
    async def usage(session: AsyncSession, tenant_id: str, project_id: str, *, name: str) -> list[dict]:
        """Entities in this project that reference secret://…/<name> - used to warn before delete."""
        refs: list[dict] = []

        aps = (await session.execute(
            select(AuthProvider).where(AuthProvider.tenant_id == tenant_id, AuthProvider.project_id == project_id)
        )).scalars()
        for ap in aps:
            if name in _referenced_names(ap.config) | _referenced_names(ap.credentials_ref):
                refs.append({"type": "auth_provider", "label": ap.name})

        tools = (await session.execute(
            select(Tool).where(Tool.tenant_id == tenant_id, Tool.project_id == project_id)
        )).scalars()
        for t in tools:
            if name in _referenced_names(t.config):
                refs.append({"type": "tool", "label": t.name})

        triggers = (await session.execute(
            select(Trigger).where(Trigger.tenant_id == tenant_id, Trigger.project_id == project_id)
        )).scalars()
        for tg in triggers:
            if name in _referenced_names(tg.config):
                refs.append({"type": "trigger", "label": tg.kind})

        proj = (await session.execute(
            select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if proj and name in _referenced_names(proj.config):
            refs.append({"type": "project_settings", "label": "provider credentials"})

        return refs

    @staticmethod
    async def delete(session: AsyncSession, tenant_id: str, project_id: str, *, name: str) -> int:
        """Hard-delete every version of the named secret in this project. Returns the count removed."""
        rows = await session.execute(
            select(Secret).where(
                Secret.tenant_id == tenant_id, Secret.project_id == project_id, Secret.name == name
            )
        )
        removed = list(rows.scalars())
        for s in removed:
            await session.delete(s)
        await session.commit()
        return len(removed)
