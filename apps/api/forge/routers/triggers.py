"""List a project's triggers (webhook URLs, schedules) for the console."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import settings
from forge.deps import current_tenant_id, get_session
from forge.models import Trigger

router = APIRouter(prefix="/v1/projects/{project_id}/triggers", tags=["triggers"])


@router.get("")
async def list_triggers(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    rows = (await session.execute(
        select(Trigger).where(Trigger.tenant_id == tenant_id, Trigger.project_id == project_id)
    )).scalars()
    base = settings.public_base_url.rstrip("/")
    out = []
    for t in rows:
        item = {
            "id": t.id, "workflow_id": t.workflow_id, "node_id": t.node_id, "kind": t.kind,
            "enabled": t.enabled, "config": t.config,
            "last_fired_at": t.last_fired_at.isoformat() if t.last_fired_at else None,
        }
        if t.kind == "webhook_in" and t.key:
            item["webhook_url"] = f"{base}/v1/hooks/{t.key}"
        out.append(item)
    return out
