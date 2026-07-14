"""Workflow endpoints (CRUD + validate)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.schemas.dto import (
    CanvasSaveIn,
    ExecutableIn,
    ValidateOut,
    WorkflowCreate,
    WorkflowOut,
    WorkflowUpdate,
)
from forge.services.workflows import WorkflowService

router = APIRouter(prefix="/v1/projects/{project_id}/workflows", tags=["workflows"])


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    return await WorkflowService.list(session, tenant_id, project_id)


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    project_id: str,
    body: WorkflowCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("editor")),
):
    return await WorkflowService.create(
        session, tenant_id, project_id,
        name=body.name, description=body.description,
        executable=body.executable, canvas=body.canvas,
    )


@router.post("/validate", response_model=ValidateOut)
async def validate_executable(project_id: str, body: ExecutableIn):
    result = WorkflowService.validate(body.executable)
    return ValidateOut(valid=result.valid, errors=result.errors, warnings=result.warnings)


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    project_id: str,
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    wf = await WorkflowService.get(session, tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    project_id: str,
    workflow_id: str,
    body: WorkflowUpdate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("editor")),
):
    wf = await WorkflowService.get(session, tenant_id, workflow_id)
    if wf is None or wf.project_id != project_id:
        raise HTTPException(404, "Workflow not found")
    name = body.name.strip() if body.name is not None else None
    if body.name is not None and not name:
        raise HTTPException(422, "Workflow name is required")
    return await WorkflowService.update(session, wf, name=name, description=body.description)


@router.put("/{workflow_id}/executable", response_model=ValidateOut)
async def update_executable(
    project_id: str,
    workflow_id: str,
    body: ExecutableIn,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("editor")),
):
    wf = await WorkflowService.get(session, tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow not found")
    result = await WorkflowService.update_executable(session, wf, body.executable, require_valid=True)
    return ValidateOut(valid=result.valid, errors=result.errors, warnings=result.warnings)


@router.post("/{workflow_id}/publish", response_model=WorkflowOut)
async def publish_workflow(
    project_id: str,
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("editor")),
):
    """Validate the stored executable and mark the workflow active (a published version)."""
    wf = await WorkflowService.get(session, tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow not found")
    result = WorkflowService.validate(wf.executable or {})
    if not result.valid:
        raise HTTPException(422, detail={"errors": result.errors})
    wf.status = "active"
    wf.active_version = (wf.active_version or 1) + 1
    await session.commit()
    await session.refresh(wf)
    await WorkflowService._sync_triggers(session, wf)  # (re)register webhook/schedule/etc.
    return wf


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    project_id: str,
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("editor")),
):
    wf = await WorkflowService.get(session, tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow not found")
    await WorkflowService.delete(session, wf)


@router.put("/{workflow_id}/canvas", response_model=ValidateOut)
async def save_canvas(
    project_id: str,
    workflow_id: str,
    body: CanvasSaveIn,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("editor")),
):
    wf = await WorkflowService.get(session, tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow not found")
    result = await WorkflowService.save_canvas(session, wf, body.canvas, body.executable)
    return ValidateOut(valid=result.valid, errors=result.errors, warnings=result.warnings)
