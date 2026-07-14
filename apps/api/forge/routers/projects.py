"""Project endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, client_ip, current_tenant_id, get_session, require_role
from forge.schemas.dto import ProjectCountsOut, ProjectCreate, ProjectOut, ProjectUpdate
from forge.services.audit import AuditService
from forge.services.projects import ProjectService
from forge.services.versions import safe_snapshot

router = APIRouter(prefix="/v1/projects", tags=["projects"])


class ProjectMemberIn(BaseModel):
    role: str  # owner|admin|editor|viewer - the caller's per-project role for {user_id}


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
    user: CurrentUser = Depends(require_role("admin")),
):
    project = await ProjectService.create(
        session, tenant_id, name=body.name, slug=body.slug,
        description=body.description, config=body.config,
    )
    await safe_snapshot(session, "project", project, author=user)
    return project


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


@router.get("/{project_id}/counts", response_model=ProjectCountsOut)
async def project_counts(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    """Lightweight per-resource counts for the project sidebar badges
    ({workflows, agents, tools, components, knowledge, auth})."""
    return await ProjectService.counts(session, tenant_id, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    user: CurrentUser = Depends(require_role("admin")),
):
    project = await ProjectService.get(session, tenant_id, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    project = await ProjectService.update(session, project, name=body.name, description=body.description, config=body.config)
    await safe_snapshot(session, "project", project, author=user)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("admin")),
):
    project = await ProjectService.get(session, tenant_id, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    await ProjectService.delete(session, project, checkpointer=getattr(request.app.state, "checkpointer", None))


# --- per-project membership / RBAC (finding h) ---
@router.get("/{project_id}/members")
async def list_project_members(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("admin")),
):
    members = await ProjectService.list_members(session, tenant_id, project_id)
    return [{"user_id": m.user_id, "role": m.role} for m in members]


@router.put("/{project_id}/members/{user_id}")
async def set_project_member(
    project_id: str,
    user_id: str,
    body: ProjectMemberIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    admin: CurrentUser = Depends(require_role("admin")),
):
    """Grant/update a user's role ON THIS PROJECT. Elevates their tenant-wide role for this
    project only (never demotes it - see deps.effective_role)."""
    if await ProjectService.get(session, tenant_id, project_id) is None:
        raise HTTPException(404, "Project not found")
    try:
        m = await ProjectService.set_member(session, tenant_id=tenant_id, project_id=project_id,
                                            user_id=user_id, role=body.role)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await AuditService.log(tenant_id=tenant_id, action="project.member.set", actor_id=admin.id,
                           actor_email=admin.email, resource_type="project", resource_id=project_id,
                           project_id=project_id, ip=client_ip(request),
                           meta={"user_id": user_id, "role": body.role})
    return {"user_id": m.user_id, "role": m.role}


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_project_member(
    project_id: str,
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    admin: CurrentUser = Depends(require_role("admin")),
):
    if not await ProjectService.remove_member(session, tenant_id=tenant_id, project_id=project_id, user_id=user_id):
        raise HTTPException(404, "membership not found")
    await AuditService.log(tenant_id=tenant_id, action="project.member.remove", actor_id=admin.id,
                           actor_email=admin.email, resource_type="project", resource_id=project_id,
                           project_id=project_id, ip=client_ip(request), meta={"user_id": user_id})
