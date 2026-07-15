"""Tool Set endpoints (CRUD + membership).

A tool set is a describable group of tools (see forge.services.tool_sets). Sets organize the
Tools screen, can be granted to an agent as a unit, and are the unit published over MCP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.models import ToolSet
from forge.schemas.dto import ToolSetCreate, ToolSetOut, ToolSetUpdate
from forge.services.tool_sets import ToolSetService

router = APIRouter(prefix="/v1/projects/{project_id}/tool-sets", tags=["tool-sets"])


def _to_out(ts: ToolSet, tool_ids: list[str]) -> ToolSetOut:
    return ToolSetOut(
        id=ts.id, project_id=ts.project_id, name=ts.name, slug=ts.slug,
        description=ts.description or "", icon=ts.icon, is_default=ts.is_default, exposed=ts.exposed, tool_ids=tool_ids,
    )


async def _load(session: AsyncSession, tenant_id: str, project_id: str, set_id: str) -> ToolSet:
    ts = await ToolSetService.get(session, tenant_id, set_id)
    if ts is None or ts.project_id != project_id:
        raise HTTPException(404, "Tool set not found")
    return ts


@router.get("", response_model=list[ToolSetOut])
async def list_tool_sets(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    sets = await ToolSetService.list(session, tenant_id, project_id)
    members = await ToolSetService.members_map(session, tenant_id, project_id)
    return [_to_out(s, members.get(s.id, [])) for s in sets]


@router.post("", response_model=ToolSetOut, status_code=201)
async def create_tool_set(project_id: str, body: ToolSetCreate, session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    ts = await ToolSetService.create(
        session, tenant_id, project_id, name=body.name, description=body.description,
        icon=body.icon, is_default=body.is_default, exposed=body.exposed, tool_ids=body.tool_ids,
    )
    return _to_out(ts, await ToolSetService.member_ids(session, tenant_id, ts.id))


@router.get("/{set_id}", response_model=ToolSetOut)
async def get_tool_set(project_id: str, set_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    ts = await _load(session, tenant_id, project_id, set_id)
    return _to_out(ts, await ToolSetService.member_ids(session, tenant_id, ts.id))


@router.patch("/{set_id}", response_model=ToolSetOut)
async def update_tool_set(project_id: str, set_id: str, body: ToolSetUpdate, session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    ts = await _load(session, tenant_id, project_id, set_id)
    ts = await ToolSetService.update(
        session, ts, name=body.name, description=body.description, icon=body.icon,
        is_default=body.is_default, exposed=body.exposed, tool_ids=body.tool_ids,
    )
    return _to_out(ts, await ToolSetService.member_ids(session, tenant_id, ts.id))


@router.delete("/{set_id}", status_code=204)
async def delete_tool_set(project_id: str, set_id: str, session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    ts = await _load(session, tenant_id, project_id, set_id)
    await ToolSetService.delete(session, ts)


@router.post("/{set_id}/tools/{tool_id}", status_code=204)
async def add_tool_to_set(project_id: str, set_id: str, tool_id: str, session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    ts = await _load(session, tenant_id, project_id, set_id)
    await ToolSetService.add_member(session, ts, tool_id)


@router.delete("/{set_id}/tools/{tool_id}", status_code=204)
async def remove_tool_from_set(project_id: str, set_id: str, tool_id: str, session: AsyncSession = Depends(get_session),
                               tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    ts = await _load(session, tenant_id, project_id, set_id)
    await ToolSetService.remove_member(session, ts, tool_id)
