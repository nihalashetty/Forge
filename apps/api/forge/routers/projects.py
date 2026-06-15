"""Project endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.schemas.dto import ProjectCreate, ProjectOut, ProjectUpdate
from forge.services.projects import ProjectService

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)
):
    return await ProjectService.list(session, tenant_id)


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    return await ProjectService.create(
        session, tenant_id, name=body.name, slug=body.slug,
        description=body.description, config=body.config,
    )


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    project = await ProjectService.get(session, tenant_id, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    project = await ProjectService.get(session, tenant_id, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return await ProjectService.update(session, project, name=body.name, description=body.description, config=body.config)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    project = await ProjectService.get(session, tenant_id, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    await ProjectService.delete(session, project, checkpointer=getattr(request.app.state, "checkpointer", None))
