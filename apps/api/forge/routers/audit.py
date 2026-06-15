"""Audit-log read endpoint (admin+)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.services.audit import AuditService

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/metrics")
async def metrics(_: CurrentUser = Depends(require_role("admin"))):
    """In-process resilience counters (swallowed-failure visibility)."""
    from forge.util.metrics import snapshot

    return snapshot()


@router.get("")
async def list_audit(
    project_id: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("admin")),
):
    rows = await AuditService.recent(session, tenant_id, project_id=project_id, limit=min(limit, 1000))
    return [
        {
            "id": r.id, "action": r.action, "actor_email": r.actor_email, "actor_id": r.actor_id,
            "resource_type": r.resource_type, "resource_id": r.resource_id, "project_id": r.project_id,
            "ip": r.ip, "status": r.status, "meta": r.meta, "at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
