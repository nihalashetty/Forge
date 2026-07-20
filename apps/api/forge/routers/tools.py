"""Tool endpoints (CRUD + /test)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.schemas.contracts import validate_against_id
from forge.schemas.dto import (
    ExportIn,
    ImportIn,
    ImportReport,
    ToolCreate,
    ToolOut,
    ToolTestIn,
    ToolUpdate,
)
from forge.services.portability import PortabilityService
from forge.services.tools import ToolService
from forge.services.versions import safe_snapshot

router = APIRouter(prefix="/v1/projects/{project_id}/tools", tags=["tools"])


@router.post("/export")
async def export_tools(project_id: str, body: ExportIn, session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id)):
    """Serialize the selected tools into a downloadable single-type bundle."""
    return await PortabilityService.export(session, tenant_id, project_id, "tool", body.ids)


@router.post("/import", response_model=ImportReport)
async def import_tools(project_id: str, body: ImportIn, session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(require_role("editor"))):
    """Create tools from an uploaded bundle in THIS project (new ids, auto-renamed on collision)."""
    if body.type not in (None, "tool"):
        raise HTTPException(422, f"This file contains '{body.type}' exports — import it from the matching screen.")
    try:
        return await PortabilityService.import_bundle(session, tenant_id, project_id, body.model_dump(), author=user)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("", response_model=list[ToolOut])
async def list_tools(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await ToolService.list(session, tenant_id, project_id)


@router.post("", response_model=ToolOut, status_code=201)
async def create_tool(project_id: str, body: ToolCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                      user: CurrentUser = Depends(require_role("editor"))):
    cfg = {**body.config, "name": body.name, "kind": body.kind}
    errors = validate_against_id(cfg, "forge/tool")
    if errors:
        raise HTTPException(422, detail={"errors": errors})
    tool = await ToolService.create(session, tenant_id, project_id, name=body.name, kind=body.kind, config=body.config, auth_provider_id=body.auth_provider_id)
    await safe_snapshot(session, "tool", tool, author=user)
    return tool


@router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(project_id: str, tool_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    return tool


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(project_id: str, tool_id: str, body: ToolUpdate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                      user: CurrentUser = Depends(require_role("editor"))):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    if body.config is not None:
        cfg = {**body.config, "name": body.name or tool.name, "kind": tool.kind}
        errors = validate_against_id(cfg, "forge/tool")
        if errors:
            raise HTTPException(422, detail={"errors": errors})
    tool = await ToolService.update(session, tool, name=body.name, config=body.config, auth_provider_id=body.auth_provider_id, enabled=body.enabled)
    await safe_snapshot(session, "tool", tool, author=user)
    return tool


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(project_id: str, tool_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                      _: CurrentUser = Depends(require_role("editor"))):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    await ToolService.delete(session, tool)


@router.post("/{tool_id}/test")
async def test_tool(project_id: str, tool_id: str, body: ToolTestIn, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                    user: CurrentUser = Depends(require_role("editor"))):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    cfg = {**(tool.config or {}), "name": tool.name, "kind": tool.kind, "auth_provider_id": tool.auth_provider_id or (tool.config or {}).get("auth_provider_id")}
    # Console tests run AS the current user, so a PER-USER auth provider resolves the tester's own
    # connected credential (end_user_id keys the per-user bundle - the same id the MCP PAT resolves
    # to). The run-context field can still override it.
    ctx = {"end_user_id": user.id, **(body.context or {})}
    result = await ToolService.test(tenant_id, project_id, cfg, body.args, ctx)
    await ToolService.record_test(session, tool, result)
    return result
