"""Live-agent inbox: list open handoffs and reply (resumes the run + pushes the answer)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import (
    CurrentUser,
    current_tenant_id,
    get_run_service,
    get_session,
    require_role,
)
from forge.models import HandoffRequest
from forge.services.handoff import HandoffService
from forge.services.runs import RunService

router = APIRouter(prefix="/v1/projects/{project_id}/handoffs", tags=["handoff"])


class ReplyIn(BaseModel):
    message: str


def _out(h: HandoffRequest) -> dict:
    return {
        "id": h.id, "run_id": h.run_id, "workflow_id": h.workflow_id, "channel_id": h.channel_id,
        "customer": h.customer, "customer_message": h.customer_message, "reason": h.reason,
        "status": h.status, "agent_id": h.agent_id,
        "at": h.created_at.isoformat() if h.created_at else None,
    }


@router.get("")
async def list_handoffs(project_id: str, status: str = "open",
                        session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("viewer"))):
    return [_out(h) for h in await HandoffService.list(session, tenant_id, project_id, status=status or None)]


@router.post("/{handoff_id}/reply")
async def reply_handoff(project_id: str, handoff_id: str, body: ReplyIn,
                        session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        user: CurrentUser = Depends(require_role("editor")),
                        run_service: RunService = Depends(get_run_service)):
    h = (await session.execute(
        select(HandoffRequest).where(HandoffRequest.tenant_id == tenant_id, HandoffRequest.id == handoff_id)
    )).scalar_one_or_none()
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "handoff not found")
    if h.status != "open":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"handoff is already {h.status}")
    return await HandoffService.reply(session, run_service, handoff=h, agent_id=user.id, message=body.message)
