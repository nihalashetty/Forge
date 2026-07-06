"""Single project-level run endpoint - the framework's simplest integration surface.

One authenticated POST runs the project's *configured* workflow (a saved project setting,
`config.api_workflow_id`) and does everything the 3-endpoint run API does, in one call:

- new turn  -> send `input` (+ `thread_id` to continue a conversation)
- HITL       -> send `resume` to answer an interrupt the workflow raised (workflow-driven, NOT
                a caller toggle)
- `stream`   -> the ONLY per-request knob: True streams SSE frames, False returns one JSON reply

Framework-generic: any project, any auth scheme. Per-user secrets (session/CSRF/bearer/etc.)
travel out-of-band in the `X-Forge-Context` header -> `{{ctx.*}}` in tools, never in the body.
Auth is the platform bearer: a service token for server-to-server callers, or a console JWT.

This is a thin wrapper over the existing run machinery (create_run + stream/resume/
run_to_completion) - it adds no new execution path, only a single friendlier surface over the
per-workflow run API at /v1/projects/{id}/workflows/{wid}/runs.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from forge.config import settings
from forge.deps import (
    CurrentUser,
    get_current_user,
    get_run_service,
    get_session,
    run_context,
)
from forge.models import Project, Run, Thread, Workflow
from forge.schemas.dto import ProjectRunIn
from forge.services.auth import role_at_least
from forge.services.runs import RunService
from forge.util.ratelimit import rate_limiter

router = APIRouter(prefix="/v1/projects/{project_id}", tags=["project-run"])

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


async def _configured_workflow(session: AsyncSession, tenant_id: str, project_id: str) -> Workflow:
    """The workflow this project's API runs: the saved `config.api_workflow_id`, else the
    active workflow, else the only one. Mirrors the embed's resolution (embed_public._workflow_id)
    so the two integration surfaces behave identically."""
    proj = (await session.execute(
        select(Project).where(Project.tenant_id == tenant_id, Project.id == project_id)
    )).scalar_one_or_none()
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    rows = (await session.execute(
        select(Workflow).where(Workflow.project_id == project_id)
    )).scalars().all()
    wid = (proj.config or {}).get("api_workflow_id")
    if wid:
        wf = next((w for w in rows if w.id == wid), None)
        if wf is not None:
            return wf
    wf = next((w for w in rows if w.status == "active"), None) or (rows[0] if rows else None)
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no workflow configured for this project's API")
    return wf


def _resolve_end_user(
    body: ProjectRunIn, user: CurrentUser, tenant_id: str, project_id: str
) -> dict | None:
    """Same identity rules as POST .../runs: a verified session token wins; else the body
    end_user, but a non-editor caller may NOT self-assert roles/entitlements (audit S4)."""
    if body.session_token:
        from forge.security import TokenError, decode_token

        try:
            claims = decode_token(body.session_token, expected_type="session")
        except TokenError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid session token: {e}") from e
        if claims.get("tid") != tenant_id or claims.get("pid") != project_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "session token is not valid for this project")
        return claims.get("end_user") or None
    if body.end_user is not None:
        eu = body.end_user.model_dump(exclude_none=True)
        if not role_at_least(user.role, "editor"):
            eu.pop("roles", None)
            eu.pop("entitlements", None)
        return eu
    return None


@router.post("/run")
async def project_run(
    project_id: str,
    body: ProjectRunIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
    run_service: RunService = Depends(get_run_service),
    rc: dict | None = Depends(run_context),
):
    tenant_id = user.tenant_id
    # Per-tenant run-creation rate limit (shared bucket with the per-workflow run API).
    if not rate_limiter.allow(f"runs:{tenant_id}", rate=settings.run_rate_limit_per_minute, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "run rate limit exceeded; slow down")

    # ---- HITL resume: answer an interrupt the workflow raised on this thread ----
    if body.resume is not None:
        if not body.thread_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "resume requires the thread_id of the interrupted conversation",
            )
        # Resolve the interrupted run by either thread handle the caller may hold - the DB
        # Thread.id or the composite LangGraph id - mirroring create_run's thread reuse.
        run = (await session.execute(
            select(Run).join(Thread, Thread.id == Run.thread_id).where(
                Run.tenant_id == tenant_id, Run.project_id == project_id,
                or_(Thread.id == body.thread_id, Thread.lg_thread_id == body.thread_id),
                Run.status == "interrupted",
            ).order_by(Run.created_at.desc())
        )).scalars().first()
        if run is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "no interrupted run to resume on this thread")
        value = body.resume.value
        if body.stream:
            async def gen_resume():
                # Lead with the canonical thread_id so the streaming caller can keep the
                # conversation going (the run frame from stream() carries the LangGraph id).
                yield {"event": "ready", "data": json.dumps({"run_id": run.id, "thread_id": run.thread_id})}
                async for frame in run_service.stream(
                    run_id=run.id, tenant_id=tenant_id, project_id=project_id,
                    run_context=rc, resume=True, resume_value=value,
                ):
                    yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str)}

            return EventSourceResponse(gen_resume(), headers=SSE_HEADERS)
        result = await run_service.resume(
            run_id=run.id, tenant_id=tenant_id, value=value, project_id=project_id, run_context=rc,
        )
        result.setdefault("run_id", run.id)
        result["thread_id"] = run.thread_id
        return result

    # ---- new turn: run the project's configured workflow ----
    wf = await _configured_workflow(session, tenant_id, project_id)
    end_user = _resolve_end_user(body, user, tenant_id, project_id)
    # Enforce the tenant DAILY quota atomically with run creation (audit F2).
    from forge.services.quota import QuotaExceeded, run_admission

    try:
        async with run_admission(session, tenant_id):
            run = await run_service.create_run(
                session, tenant_id=tenant_id, project_id=project_id, workflow_id=wf.id,
                input=body.input or {}, thread_id=body.thread_id, end_user=end_user,
            )
    except QuotaExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, e.message) from e

    if body.stream:
        async def gen_new():
            yield {"event": "ready", "data": json.dumps({"run_id": run.id, "thread_id": run.thread_id})}
            async for frame in run_service.stream(
                run_id=run.id, tenant_id=tenant_id, project_id=project_id, run_context=rc,
            ):
                yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str)}

        return EventSourceResponse(gen_new(), headers=SSE_HEADERS)

    result = await run_service.run_to_completion(
        run_id=run.id, tenant_id=tenant_id, project_id=project_id, run_context=rc,
    )
    result["thread_id"] = run.thread_id
    return result
