"""Secret endpoints - write-only; plaintext is never returned."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.schemas.dto import SecretCreate, SecretOut
from forge.services.secrets import SecretService

router = APIRouter(prefix="/v1/projects/{project_id}/secrets", tags=["secrets"])


@router.get("", response_model=list[SecretOut])
async def list_secrets(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await SecretService.list(session, tenant_id, project_id)


@router.post("", response_model=SecretOut, status_code=201)
async def create_secret(project_id: str, body: SecretCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("admin"))):
    return await SecretService.write(session, tenant_id, project_id, name=body.name, value=body.value, kind=body.kind)


@router.get("/{name}/usage")
async def secret_usage(project_id: str, name: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    refs = await SecretService.usage(session, tenant_id, project_id, name=name)
    return {"count": len(refs), "references": refs}


@router.delete("/{name}", status_code=204)
async def delete_secret(project_id: str, name: str, force: bool = False, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("admin"))):
    if not force:
        refs = await SecretService.usage(session, tenant_id, project_id, name=name)
        if refs:
            raise HTTPException(status.HTTP_409_CONFLICT, detail={"message": "Secret is in use", "references": refs})
    removed = await SecretService.delete(session, tenant_id, project_id, name=name)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Secret not found")
