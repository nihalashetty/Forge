"""Run endpoints — create a run and stream its execution over SSE."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from forge.config import settings
from forge.deps import current_tenant_id, get_run_service, get_session
from forge.schemas.dto import ResumeIn, RunCreate, RunOut
from forge.services.runs import RunService
from forge.util.ratelimit import idempotency, rate_limiter

router = APIRouter(prefix="/v1/projects/{project_id}/workflows/{workflow_id}/runs", tags=["runs"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


@router.post("", response_model=RunOut, status_code=201)
async def create_run(
    project_id: str,
    workflow_id: str,
    body: RunCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    run_service: RunService = Depends(get_run_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # Idempotency: a retried POST with the same key returns the original run instead
    # of starting a duplicate (important for at-least-once callers / channel webhooks).
    if idempotency_key:
        cache_key = f"run:{tenant_id}:{idempotency_key}"
        cached = idempotency.get(cache_key)
        if cached is not None:
            return cached

    # Per-tenant run-creation rate limit.
    if not rate_limiter.allow(f"runs:{tenant_id}", rate=settings.run_rate_limit_per_minute, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "run rate limit exceeded; slow down")

    # Per-tenant DAILY quota (runs / cost / tokens), from tenant.settings.
    from forge.services.quota import QuotaExceeded, check_run_quota
    try:
        await check_run_quota(session, tenant_id)
    except QuotaExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, e.message) from e

    run = await run_service.create_run(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        workflow_id=workflow_id,
        input=body.input or {},
        thread_id=body.thread_id,
    )
    out = RunOut(id=run.id, status=run.status, thread_id=run.thread_id)
    if idempotency_key:
        idempotency.put(f"run:{tenant_id}:{idempotency_key}", out)
    return out


@router.get("/{run_id}/stream")
async def stream_run(
    project_id: str,
    workflow_id: str,
    run_id: str,
    tenant_id: str = Depends(current_tenant_id),
    run_service: RunService = Depends(get_run_service),
):
    async def event_gen():
        async for frame in run_service.stream(run_id=run_id, tenant_id=tenant_id):
            yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str)}

    return EventSourceResponse(event_gen(), headers=SSE_HEADERS)


@router.post("/{run_id}/rerun", response_model=RunOut, status_code=201)
async def rerun(
    project_id: str,
    workflow_id: str,
    run_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    run_service: RunService = Depends(get_run_service),
):
    """Replay a past run: create a new run with the same input (fresh thread)."""
    from sqlalchemy import select

    from forge.models import Run

    orig = (await session.execute(select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id))).scalar_one_or_none()
    if orig is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    run = await run_service.create_run(
        session, tenant_id=tenant_id, project_id=project_id, workflow_id=workflow_id, input=orig.input or {},
    )
    return RunOut(id=run.id, status=run.status, thread_id=run.thread_id)


@router.post("/{run_id}/resume")
async def resume_run(
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: ResumeIn,
    tenant_id: str = Depends(current_tenant_id),
    run_service: RunService = Depends(get_run_service),
):
    return await run_service.resume(run_id=run_id, tenant_id=tenant_id, value=body.value)
