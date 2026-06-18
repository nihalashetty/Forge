"""Embed / identity endpoints (Phase 3b).

Mint a short-lived, signed SESSION TOKEN that carries a verified `end_user` for the browser
widget. Called server-to-server by the integrator's authenticated backend (which already
authenticated the user); the widget then sends the token on each run so Forge trusts the
identity without trusting the browser. The signing secret never reaches the client.
"""

from __future__ import annotations

import secrets as _secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, get_session, require_role
from forge.schemas.dto import EndUser
from forge.security import create_session_token
from forge.services.projects import ProjectService

router = APIRouter(prefix="/v1/projects/{project_id}", tags=["embed"])


class SessionTokenIn(BaseModel):
    end_user: EndUser
    ttl_minutes: int = Field(default=30, ge=1, le=720)
    # Origins the widget is allowed to run from (carried as a claim for the widget transport;
    # see the deferred per-origin CORS work). Empty = unrestricted by token.
    origins: list[str] = Field(default_factory=list)


class SessionTokenOut(BaseModel):
    token: str
    expires_in: int  # seconds


@router.post("/session-tokens", response_model=SessionTokenOut)
async def mint_session_token(
    project_id: str,
    body: SessionTokenIn,
    user: CurrentUser = Depends(require_role("editor")),
) -> SessionTokenOut:
    token = create_session_token(
        tenant_id=user.tenant_id,
        project_id=project_id,
        end_user=body.end_user.model_dump(exclude_none=True),
        origins=body.origins,
        ttl_minutes=body.ttl_minutes,
    )
    return SessionTokenOut(token=token, expires_in=body.ttl_minutes * 60)


# --- embeddable widget settings (publishable key + allowed origins + workflow) ---
class EmbedSettingsIn(BaseModel):
    enabled: bool = True
    allowed_origins: list[str] = Field(default_factory=list)
    workflow_id: str | None = None


class EmbedSettingsOut(BaseModel):
    enabled: bool
    allowed_origins: list[str]
    workflow_id: str | None = None
    publishable_key: str | None = None
    embed_src: str | None = None


def _embed_out(project) -> EmbedSettingsOut:
    e = (project.config or {}).get("embed") or {}
    key = project.embed_key
    enabled = bool(e.get("enabled"))
    return EmbedSettingsOut(
        enabled=enabled,
        allowed_origins=e.get("allowed_origins") or [],
        workflow_id=e.get("workflow_id"),
        publishable_key=key,
        embed_src=(f"/embed?key={key}" if key and enabled else None),
    )


@router.get("/embed", response_model=EmbedSettingsOut)
async def get_embed(project_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(require_role("editor"))):
    proj = await ProjectService.get(session, user.tenant_id, project_id)
    if proj is None:
        raise HTTPException(404, "Project not found")
    return _embed_out(proj)


@router.put("/embed", response_model=EmbedSettingsOut)
async def set_embed(project_id: str, body: EmbedSettingsIn, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(require_role("editor"))):
    proj = await ProjectService.get(session, user.tenant_id, project_id)
    if proj is None:
        raise HTTPException(404, "Project not found")
    if proj.embed_key is None:
        proj.embed_key = "pk_" + _secrets.token_urlsafe(24)
    cfg = dict(proj.config or {})
    cfg["embed"] = {
        "enabled": body.enabled,
        "allowed_origins": [o.strip() for o in (body.allowed_origins or []) if o.strip()],
        "workflow_id": body.workflow_id,
    }
    proj.config = cfg
    await session.commit()
    await session.refresh(proj)
    return _embed_out(proj)
