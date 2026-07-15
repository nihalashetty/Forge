"""Run endpoints - create a run and stream its execution over SSE."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from forge.config import settings
from forge.deps import (
    CurrentUser,
    current_tenant_id,
    get_current_user,
    get_run_service,
    get_session,
    run_context,
)
from forge.schemas.dto import ResumeIn, RunCreate, RunOut
from forge.services.auth import role_at_least
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
    user: CurrentUser = Depends(get_current_user),
    run_service: RunService = Depends(get_run_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    tenant_id = user.tenant_id
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

    # Resolve the end-user identity (Phase 3b): a verified session token (browser widget)
    # wins; else the body end_user. Body identity is only fully trusted for editor+ callers
    # (server-to-server integrators); a lower-privilege console user may NOT self-assert
    # roles/entitlements that gate tools (audit S4) - those fields are stripped.
    end_user = None
    if body.session_token:
        from forge.security import TokenError, decode_token

        try:
            claims = decode_token(body.session_token, expected_type="session")
        except TokenError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid session token: {e}") from e
        if claims.get("tid") != tenant_id or claims.get("pid") != project_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "session token is not valid for this project")
        end_user = claims.get("end_user") or None
    elif body.end_user is not None:
        end_user = body.end_user.model_dump(exclude_none=True)
        if not role_at_least(user.role, "editor"):
            for privileged in ("roles", "entitlements"):
                if end_user.pop(privileged, None) is not None:
                    pass  # silently dropped - a viewer can't escalate via the run body

    # Per-tenant DAILY quota, enforced atomically with run creation so concurrent POSTs
    # can't all pass a stale pre-insert count (audit F2).
    from forge.services.budget import BudgetExceeded, ModelNotAllowed
    from forge.services.quota import QuotaExceeded, run_admission
    try:
        async with run_admission(session, tenant_id):
            run = await run_service.create_run(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                workflow_id=workflow_id,
                input=body.input or {},
                thread_id=body.thread_id,
                end_user=end_user,
                source="playground",
            )
    except QuotaExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, e.message) from e
    except ModelNotAllowed as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, e.message) from e
    except BudgetExceeded as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, e.message) from e
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
    rc: dict | None = Depends(run_context),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    # Reconnect/reattach: a browser's EventSource resends the last id it saw as Last-Event-ID;
    # we replay frames after it then follow live. Absent/garbage -> start from the beginning.
    start_from = int(last_event_id) if (last_event_id or "").isdigit() else 0

    async def event_gen():
        async for frame in run_service.stream(
            run_id=run_id, tenant_id=tenant_id, project_id=project_id, run_context=rc,
            last_event_id=start_from,
        ):
            yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str), "id": frame.get("id")}

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

    orig = (await session.execute(select(Run).where(
        Run.tenant_id == tenant_id,
        Run.project_id == project_id,
        Run.workflow_id == workflow_id,
        Run.id == run_id,
    ))).scalar_one_or_none()
    if orig is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    run = await run_service.create_run(
        session, tenant_id=tenant_id, project_id=project_id, workflow_id=workflow_id, input=orig.input or {},
        source=getattr(orig, "source", None) or "playground",
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
    rc: dict | None = Depends(run_context),
):
    return await run_service.resume(run_id=run_id, tenant_id=tenant_id, value=body.value, project_id=project_id, run_context=rc)


@router.post("/{run_id}/cancel")
async def cancel_run(
    project_id: str,
    workflow_id: str,
    run_id: str,
    tenant_id: str = Depends(current_tenant_id),
    run_service: RunService = Depends(get_run_service),
    _: CurrentUser = Depends(get_current_user),
):
    """Cancel a run: mark it canceled and cooperatively stop it (frees the tenant-concurrency
    slot). A terminal run (done/error/canceled) returns ok=False with its current status."""
    result = await run_service.cancel_run(run_id=run_id, tenant_id=tenant_id, project_id=project_id)
    if result.get("error") == "run not found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return result
