"""Public embed transport (Phase 3b/4).

Key-gated, NOT platform-JWT-authenticated endpoints the chat widget calls. The project is
resolved by its publishable key; the widget can only run the project's configured workflow,
as an anonymous end user or one verified by a server-minted session token. Rate-limited per
key. The widget is served same-origin from /embed, so these are same-origin calls (no CORS).
Embedding-site restriction is enforced by the /embed page's `frame-ancestors` CSP, set from
the project's allowed_origins (see the web middleware).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from forge.config import settings
from forge.deps import client_ip, get_run_service, get_session
from forge.models import Project, Workflow
from forge.security import TokenError, decode_token
from forge.services.components import ComponentService
from forge.services.runs import RunService
from forge.util.ratelimit import rate_limiter

router = APIRouter(prefix="/v1/embed/{key}", tags=["embed-public"])

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def _embed_rate_limit(key: str, ip: str | None, *, per_min: int, ip_per_min: int) -> None:
    """The publishable key is PUBLIC, so the real cost/abuse ceilings are the per-IP and
    per-key limits (audit S2). Raises 429 when either bucket is empty. per_min/ip_per_min
    of 0 => that bucket is unlimited."""
    if per_min and not rate_limiter.allow(f"embed:{key}", rate=per_min, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests; slow down")
    if ip_per_min and not rate_limiter.allow(f"embed-ip:{key}:{ip or 'unknown'}", rate=ip_per_min, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests from your network; slow down")


async def _project(session: AsyncSession, key: str) -> Project:
    proj = (await session.execute(select(Project).where(Project.embed_key == key))).scalar_one_or_none()
    if proj is None or not ((proj.config or {}).get("embed") or {}).get("enabled"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "embed not found or disabled")
    return proj


async def _workflow_id(session: AsyncSession, proj: Project) -> str:
    e = (proj.config or {}).get("embed") or {}
    if e.get("workflow_id"):
        return e["workflow_id"]
    rows = (await session.execute(select(Workflow).where(Workflow.project_id == proj.id))).scalars().all()
    active = next((w for w in rows if w.status == "active"), None) or (rows[0] if rows else None)
    if active is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no workflow configured for this embed")
    return active.id


class EmbedConfigOut(BaseModel):
    name: str
    allowed_origins: list[str] = []


class EmbedRunIn(BaseModel):
    input: dict | None = None
    thread_id: str | None = None
    session_token: str | None = None


@router.get("/config", response_model=EmbedConfigOut)
async def embed_config(key: str, session: AsyncSession = Depends(get_session)):
    proj = await _project(session, key)
    e = (proj.config or {}).get("embed") or {}
    # workflow_id is resolved server-side from the key on each run - the widget never needs it, so
    # don't leak the internal id to the anonymous client (audit L).
    return EmbedConfigOut(name=proj.name, allowed_origins=e.get("allowed_origins") or [])


@router.get("/components")
async def embed_components(key: str, session: AsyncSession = Depends(get_session)):
    proj = await _project(session, key)
    comps = await ComponentService.list(session, proj.tenant_id, proj.id)
    return [{"id": c.id, "name": c.name, "html": c.html, "css": c.css, "actions": c.actions} for c in comps]


@router.post("/runs")
async def embed_create_run(key: str, body: EmbedRunIn, request: Request, session: AsyncSession = Depends(get_session), run_service: RunService = Depends(get_run_service)):
    proj = await _project(session, key)
    _embed_rate_limit(
        key, client_ip(request),
        per_min=settings.embed_rate_limit_per_minute,
        ip_per_min=settings.embed_rate_limit_per_ip_per_minute,
    )
    wid = await _workflow_id(session, proj)
    # Identity: only from a verified session token (the browser can't assert it); else anonymous.
    end_user = None
    if body.session_token:
        try:
            claims = decode_token(body.session_token, expected_type="session")
        except TokenError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid session token: {e}") from e
        if claims.get("tid") != proj.tenant_id or claims.get("pid") != proj.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "session token is not valid for this embed")
        end_user = claims.get("end_user") or None
    # The public surface is anonymous and uncapped by design, so it MUST also honor the
    # tenant daily quota (audit S2) - otherwise the widget bypasses the only spend ceiling.
    from forge.services.quota import QuotaExceeded, run_admission
    try:
        async with run_admission(session, proj.tenant_id):
            run = await run_service.create_run(
                session, tenant_id=proj.tenant_id, project_id=proj.id, workflow_id=wid,
                input=body.input or {}, thread_id=body.thread_id, end_user=end_user, source="embed",
            )
    except QuotaExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, e.message) from e
    return {"id": run.id, "thread_id": run.thread_id}


@router.get("/runs/{run_id}/stream")
async def embed_stream(key: str, run_id: str, request: Request, session: AsyncSession = Depends(get_session), run_service: RunService = Depends(get_run_service)):
    proj = await _project(session, key)
    _embed_rate_limit(
        key, client_ip(request),
        per_min=0,  # connection churn is bounded per-IP below, not per-key
        ip_per_min=settings.embed_stream_limit_per_ip_per_minute,
    )

    async def gen():
        # Scope by BOTH tenant and project (audit S1) so a publishable key can't stream
        # another project's runs; public=True hides internal error detail / operator data.
        # run_context is NOT taken from the anonymous browser: X-Forge-Context is a trusted
        # server-side caller channel, so honoring it here would let an end user forge {{ctx.*}}
        # values injected into outbound tool requests (audit M4).
        async for frame in run_service.stream(
            run_id=run_id, tenant_id=proj.tenant_id, project_id=proj.id, public=True, run_context=None,
        ):
            yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str)}

    return EventSourceResponse(gen(), headers=SSE_HEADERS)


class EmbedResumeIn(BaseModel):
    value: Any = True


@router.post("/runs/{run_id}/resume")
async def embed_resume(key: str, run_id: str, body: EmbedResumeIn, request: Request, session: AsyncSession = Depends(get_session), run_service: RunService = Depends(get_run_service)):
    """Resume an interrupted (human-in-the-loop) run from the widget - mirrors the authed
    resume endpoint but resolves the tenant+project from the publishable key. resume() is
    scoped to this project (audit S1); the end-user identity is already bound to the thread
    (thread.meta.end_user) from the original run, so a value-only body matches the authed
    resume exactly."""
    proj = await _project(session, key)
    _embed_rate_limit(
        key, client_ip(request),
        per_min=settings.embed_rate_limit_per_minute,
        ip_per_min=settings.embed_rate_limit_per_ip_per_minute,
    )
    # public=True redacts internal error detail (M6) and returns only the final assistant message
    # (H5); run_context=None so the anonymous caller can't inject {{ctx.*}} tool values (M4).
    return await run_service.resume(run_id=run_id, tenant_id=proj.tenant_id, value=body.value, project_id=proj.id, run_context=None, public=True)
