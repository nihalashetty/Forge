"""AuditService - append-only audit trail (Doc 2 §12).

Writes in its own session so an audit record persists independently of the caller's
transaction, and never raises (auditing must not break the request it records).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from forge.db.base import SessionLocal
from forge.models import AuditLog

log = logging.getLogger("forge.audit")


class AuditService:
    @staticmethod
    async def log(
        *,
        tenant_id: str,
        action: str,
        actor_id: str | None = None,
        actor_email: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        project_id: str | None = None,
        ip: str | None = None,
        status: str = "ok",
        meta: dict[str, Any] | None = None,
    ) -> None:
        try:
            async with SessionLocal() as s:
                s.add(AuditLog(
                    tenant_id=tenant_id, action=action, actor_id=actor_id,
                    actor_email=actor_email, resource_type=resource_type,
                    resource_id=resource_id, project_id=project_id, ip=ip,
                    status=status, meta=meta or {},
                ))
                await s.commit()
        except Exception:  # noqa: BLE001 - auditing must never break the request
            log.exception("audit write failed for action=%s", action)

    @staticmethod
    async def recent(session, tenant_id: str, *, project_id: str | None = None, limit: int = 200) -> list[AuditLog]:
        q = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if project_id:
            q = q.where(AuditLog.project_id == project_id)
        q = q.order_by(AuditLog.created_at.desc()).limit(limit)
        return list((await session.execute(q)).scalars())
