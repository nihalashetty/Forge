"""Tool endpoints (CRUD + /test)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.schemas.contracts import validate_against_id
from forge.schemas.dto import ToolCreate, ToolOut, ToolTestIn, ToolUpdate
from forge.services.tools import ToolService

router = APIRouter(prefix="/v1/projects/{project_id}/tools", tags=["tools"])


@router.get("", response_model=list[ToolOut])
async def list_tools(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await ToolService.list(session, tenant_id, project_id)


@router.post("", response_model=ToolOut, status_code=201)
async def create_tool(project_id: str, body: ToolCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    cfg = {**body.config, "name": body.name, "kind": body.kind}
    errors = validate_against_id(cfg, "forge/tool")
    if errors:
        raise HTTPException(422, detail={"errors": errors})
    return await ToolService.create(session, tenant_id, project_id, name=body.name, kind=body.kind, config=body.config, auth_provider_id=body.auth_provider_id)


@router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(project_id: str, tool_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    return tool


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(project_id: str, tool_id: str, body: ToolUpdate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    if body.config is not None:
        cfg = {**body.config, "name": body.name or tool.name, "kind": tool.kind}
        errors = validate_against_id(cfg, "forge/tool")
        if errors:
            raise HTTPException(422, detail={"errors": errors})
    return await ToolService.update(session, tool, name=body.name, config=body.config, auth_provider_id=body.auth_provider_id, enabled=body.enabled)


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(project_id: str, tool_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    await ToolService.delete(session, tool)


@router.post("/{tool_id}/test")
async def test_tool(project_id: str, tool_id: str, body: ToolTestIn, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    tool = await ToolService.get(session, tenant_id, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    cfg = {**(tool.config or {}), "name": tool.name, "kind": tool.kind, "auth_provider_id": tool.auth_provider_id or (tool.config or {}).get("auth_provider_id")}
    result = await ToolService.test(tenant_id, project_id, cfg, body.args, body.context)
    await ToolService.record_test(session, tool, result)
    return result
