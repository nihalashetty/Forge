"""Self-service per-user credentials ("connections") — a connector-safe surface.

A per-user auth provider (config.per_user_context_keys = ["end_user_id"]) has NO shared secret;
each end user supplies their OWN downstream token and a tool then acts as them. These routes let
ANY logged-in user — down to the least-privileged `connector` role, who never sees the Auth
Providers admin — list the per-user providers they must connect and set/clear their own token.

Deliberately separate from the `/auth-providers` admin router: it returns only minimal fields (no
provider config / secret refs) and is gated at "any real logged-in user", so a connector needs no
access to the auth-provider admin surface. Keyed server-side by the CALLER's user id — the same
identity an MCP PAT resolves to — so the token a user pastes here is exactly what gets injected on
tool calls made as them. Setting a credential ON BEHALF OF another user stays editor-gated on the
`/auth-providers/{ap_id}/connections/{end_user_id}` routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_current_user, get_session
from forge.schemas.dto import UserConnectionIn
from forge.services.auth_providers import AuthProviderService

router = APIRouter(prefix="/v1/projects/{project_id}/connections", tags=["connections"])


def _require_real_user(user: CurrentUser) -> None:
    # A per-user credential is keyed by the caller's stable user id. Reject only shared machine
    # principals (service token / API key) which carry no per-user identity; a logged-in user
    # (any role, incl. connector) AND the auth-off dev user both key fine.
    if str(user.id).startswith(("apikey:", "service")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "per-user credentials require a user identity")


def _is_per_user(ap) -> bool:
    return "end_user_id" in ((ap.config or {}).get("per_user_context_keys") or [])


async def _load_per_user(session, tenant_id: str, ap_id: str, user: CurrentUser):
    _require_real_user(user)
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "auth provider not found")
    if not _is_per_user(ap):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "auth provider is not per-user")
    return ap


@router.get("")
async def list_my_connections(project_id: str, session: AsyncSession = Depends(get_session),
                              tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(get_current_user)):
    """The per-user providers this project defines, each with whether the CALLER has connected it.
    Minimal fields only (no config / secret refs) — safe for the connector home page."""
    _require_real_user(user)
    aps = await AuthProviderService.list(session, tenant_id, project_id)
    out = []
    for ap in aps:
        if not _is_per_user(ap):
            continue
        st = await AuthProviderService.get_user_connection(tenant_id, project_id, ap, user.id)
        out.append({"id": ap.id, "name": ap.name, "kind": ap.kind, "connected": bool(st.get("connected"))})
    return out


@router.get("/{ap_id}")
async def my_connection_status(project_id: str, ap_id: str, session: AsyncSession = Depends(get_session),
                               tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(get_current_user)):
    ap = await _load_per_user(session, tenant_id, ap_id, user)
    return await AuthProviderService.get_user_connection(tenant_id, project_id, ap, user.id)


@router.put("/{ap_id}", status_code=204)
async def set_my_connection(project_id: str, ap_id: str, body: UserConnectionIn, session: AsyncSession = Depends(get_session),
                            tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(get_current_user)):
    ap = await _load_per_user(session, tenant_id, ap_id, user)
    bundle = {"access_token": body.access_token, **(body.extra or {})}
    if body.refresh_token:
        bundle["refresh_token"] = body.refresh_token
    if body.expires_at is not None:
        bundle["expires_at"] = body.expires_at
    await AuthProviderService.set_user_connection(session, tenant_id, project_id, ap, user.id, bundle=bundle)


@router.delete("/{ap_id}", status_code=204)
async def clear_my_connection(project_id: str, ap_id: str, session: AsyncSession = Depends(get_session),
                              tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(get_current_user)):
    ap = await _load_per_user(session, tenant_id, ap_id, user)
    await AuthProviderService.clear_user_connection(session, tenant_id, project_id, ap, user.id)
