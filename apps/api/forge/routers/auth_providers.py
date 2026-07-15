"""Auth Provider endpoints (CRUD + /test)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.schemas.contracts import validate_against_id
from forge.schemas.dto import (
    AuthProviderCreate,
    AuthProviderOut,
    AuthProviderUpdate,
    AuthTestIn,
    UserConnectionIn,
)
from forge.services.auth_providers import AuthProviderService
from forge.services.versions import safe_snapshot

router = APIRouter(prefix="/v1/projects/{project_id}/auth-providers", tags=["auth-providers"])


@router.get("", response_model=list[AuthProviderOut])
async def list_aps(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await AuthProviderService.list(session, tenant_id, project_id)


@router.post("", response_model=AuthProviderOut, status_code=201)
async def create_ap(project_id: str, body: AuthProviderCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                    user: CurrentUser = Depends(require_role("editor"))):
    cfg = {**body.config, "name": body.name, "kind": body.kind}
    if body.credentials_ref:
        cfg["credentials_ref"] = body.credentials_ref
    errors = validate_against_id(cfg, "forge/auth_provider")
    if errors:
        raise HTTPException(422, detail={"errors": errors})
    ap = await AuthProviderService.create(session, tenant_id, project_id, name=body.name, kind=body.kind, config=body.config, credentials_ref=body.credentials_ref)
    await safe_snapshot(session, "auth_provider", ap, author=user)
    return ap


@router.get("/{ap_id}", response_model=AuthProviderOut)
async def get_ap(project_id: str, ap_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    return ap


@router.patch("/{ap_id}", response_model=AuthProviderOut)
async def update_ap(project_id: str, ap_id: str, body: AuthProviderUpdate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                    user: CurrentUser = Depends(require_role("editor"))):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    if body.config is not None:
        cfg = {**body.config, "name": body.name or ap.name, "kind": body.kind or ap.kind}
        if body.credentials_ref:
            cfg["credentials_ref"] = body.credentials_ref
        errors = validate_against_id(cfg, "forge/auth_provider")
        if errors:
            raise HTTPException(422, detail={"errors": errors})
    ap = await AuthProviderService.update(session, ap, name=body.name, kind=body.kind, config=body.config, credentials_ref=body.credentials_ref)
    await safe_snapshot(session, "auth_provider", ap, author=user)
    return ap


@router.delete("/{ap_id}", status_code=204)
async def delete_ap(project_id: str, ap_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                    _: CurrentUser = Depends(require_role("editor"))):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    await AuthProviderService.delete(session, ap)


@router.post("/{ap_id}/test")
async def test_ap(project_id: str, ap_id: str, body: AuthTestIn, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                  _: CurrentUser = Depends(require_role("editor"))):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    return await AuthProviderService.test(tenant_id, project_id, ap, body.context)


# --- Per-user connected credentials: the app owner's connect flow stores each end user's downstream
# credential here (server-to-server, editor+), and the AuthResolver uses it to act as that user. ---
@router.put("/{ap_id}/connections/{end_user_id}", status_code=204)
async def set_user_connection(project_id: str, ap_id: str, end_user_id: str, body: UserConnectionIn,
                              session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                              _: CurrentUser = Depends(require_role("editor"))):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    bundle = {"access_token": body.access_token, **(body.extra or {})}
    if body.refresh_token:
        bundle["refresh_token"] = body.refresh_token
    if body.expires_at is not None:
        bundle["expires_at"] = body.expires_at
    await AuthProviderService.set_user_connection(session, tenant_id, project_id, ap, end_user_id, bundle=bundle)


@router.get("/{ap_id}/connections/{end_user_id}")
async def get_user_connection(project_id: str, ap_id: str, end_user_id: str,
                              session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    return await AuthProviderService.get_user_connection(tenant_id, project_id, ap, end_user_id)


@router.delete("/{ap_id}/connections/{end_user_id}", status_code=204)
async def delete_user_connection(project_id: str, ap_id: str, end_user_id: str,
                                 session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                                 _: CurrentUser = Depends(require_role("editor"))):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    await AuthProviderService.clear_user_connection(session, tenant_id, project_id, ap, end_user_id)
