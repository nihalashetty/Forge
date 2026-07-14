"""Audit-log read + export endpoints (admin+).

The audit trail is APPEND-ONLY: these endpoints only ever read it (there is deliberately no
update/delete route). See services/audit.py and infra/postgres_rls.sql for the invariant.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.services.audit import AuditService

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/metrics")
async def metrics(_: CurrentUser = Depends(require_role("admin"))):
    """In-process resilience counters (swallowed-failure visibility)."""
    from forge.util.metrics import snapshot

    return snapshot()


def _row(r) -> dict:
    return {
        "id": r.id, "action": r.action, "actor_email": r.actor_email, "actor_id": r.actor_id,
        "resource_type": r.resource_type, "resource_id": r.resource_id, "project_id": r.project_id,
        "ip": r.ip, "status": r.status, "meta": r.meta, "at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("")
async def list_audit(
    response: Response,
    project_id: str | None = None,
    action: str | None = None,
    actor: str | None = Query(None, description="match actor_email or actor_id"),
    start: datetime | None = Query(None, description="only entries at/after this time (ISO 8601)"),
    end: datetime | None = Query(None, description="only entries at/before this time (ISO 8601)"),
    cursor: str | None = Query(None, description="opaque keyset cursor from a prior page"),
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("admin")),
):
    """Filtered, keyset-paginated audit list (newest first). Returns a JSON array (unchanged
    shape); the opaque cursor for the next page, when there is one, is in the `X-Next-Cursor`
    response header (finding g)."""
    try:
        rows, next_cursor = await AuditService.query(
            session, tenant_id, action=action, actor=actor, project_id=project_id,
            start=start, end=end, cursor=cursor, limit=min(limit, 1000),
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return [_row(r) for r in rows]


@router.get("/export")
async def export_audit(
    project_id: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("admin")),
):
    """Stream ALL matching audit rows as newline-delimited JSON (oldest first), paged internally
    so a large export never buffers the whole table (finding g)."""

    async def _gen():
        async for r in AuditService.export(session, tenant_id, action=action, actor=actor,
                                           project_id=project_id, start=start, end=end):
            yield json.dumps(_row(r), default=str) + "\n"

    return StreamingResponse(
        _gen(), media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=forge-audit.ndjson"},
    )
