"""Trace endpoints — runs list + span detail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.schemas.dto import TraceDetailOut, TraceOut
from forge.services.traces import TraceService

router = APIRouter(prefix="/v1/projects/{project_id}/traces", tags=["traces"])


@router.get("", response_model=list[TraceOut])
async def list_traces(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await TraceService.list(session, tenant_id, project_id)


@router.get("/{trace_id}", response_model=TraceDetailOut)
async def get_trace(project_id: str, trace_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    trace = await TraceService.get(session, tenant_id, trace_id)
    if trace is None:
        raise HTTPException(404, "Trace not found")
    spans = await TraceService.spans(session, tenant_id, trace_id)
    return {"trace": trace, "spans": spans}
