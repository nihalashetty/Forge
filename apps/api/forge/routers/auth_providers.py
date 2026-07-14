"""Auth Provider endpoints (CRUD + /test)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.schemas.contracts import validate_against_id
from forge.schemas.dto import AuthProviderCreate, AuthProviderOut, AuthProviderUpdate, AuthTestIn
from forge.services.auth_providers import AuthProviderService

router = APIRouter(prefix="/v1/projects/{project_id}/auth-providers", tags=["auth-providers"])


@router.get("", response_model=list[AuthProviderOut])
async def list_aps(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await AuthProviderService.list(session, tenant_id, project_id)


@router.post("", response_model=AuthProviderOut, status_code=201)
async def create_ap(project_id: str, body: AuthProviderCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                    _: CurrentUser = Depends(require_role("editor"))):
    cfg = {**body.config, "name": body.name, "kind": body.kind}
    if body.credentials_ref:
        cfg["credentials_ref"] = body.credentials_ref
    errors = validate_against_id(cfg, "forge/auth_provider")
    if errors:
        raise HTTPException(422, detail={"errors": errors})
    return await AuthProviderService.create(session, tenant_id, project_id, name=body.name, kind=body.kind, config=body.config, credentials_ref=body.credentials_ref)


@router.get("/{ap_id}", response_model=AuthProviderOut)
async def get_ap(project_id: str, ap_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    ap = await AuthProviderService.get(session, tenant_id, ap_id)
    if ap is None:
        raise HTTPException(404, "Auth provider not found")
    return ap


@router.patch("/{ap_id}", response_model=AuthProviderOut)
async def update_ap(project_id: str, ap_id: str, body: AuthProviderUpdate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                    _: CurrentUser = Depends(require_role("editor"))):
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
    return await AuthProviderService.update(session, ap, name=body.name, kind=body.kind, config=body.config, credentials_ref=body.credentials_ref)


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
