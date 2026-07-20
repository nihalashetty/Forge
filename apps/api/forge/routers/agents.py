"""Agent preset endpoints (CRUD + validate)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.schemas.dto import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
    ExportIn,
    ImportIn,
    ImportReport,
    ValidateOut,
)
from forge.services.agents import AgentService
from forge.services.portability import PortabilityService
from forge.services.versions import safe_snapshot

router = APIRouter(prefix="/v1/projects/{project_id}/agents", tags=["agents"])


@router.post("/export")
async def export_agents(project_id: str, body: ExportIn, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id)):
    """Serialize the selected agent presets (full config) into a downloadable bundle."""
    return await PortabilityService.export(session, tenant_id, project_id, "agent", body.ids)


@router.post("/import", response_model=ImportReport)
async def import_agents(project_id: str, body: ImportIn, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(require_role("editor"))):
    """Create agent presets from an uploaded bundle in THIS project (auto-renamed on collision)."""
    if body.type not in (None, "agent"):
        raise HTTPException(422, f"This file contains '{body.type}' exports — import it from the matching screen.")
    try:
        return await PortabilityService.import_bundle(session, tenant_id, project_id, body.model_dump(), author=user)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("", response_model=list[AgentOut])
async def list_agents(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await AgentService.list(session, tenant_id, project_id)


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(project_id: str, body: AgentCreate, session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(require_role("editor"))):
    agent = await AgentService.create(session, tenant_id, project_id, name=body.name, config=body.config,
                                      created_by=user.id, created_by_email=user.email)
    await safe_snapshot(session, "agent", agent, author=user)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(project_id: str, agent_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    agent = await AgentService.get(session, tenant_id, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(project_id: str, agent_id: str, body: AgentUpdate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                       user: CurrentUser = Depends(require_role("editor"))):
    agent = await AgentService.get(session, tenant_id, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    agent = await AgentService.update(session, agent, name=body.name, config=body.config)
    await safe_snapshot(session, "agent", agent, author=user)
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(project_id: str, agent_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                       _: CurrentUser = Depends(require_role("editor"))):
    agent = await AgentService.get(session, tenant_id, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    await AgentService.delete(session, agent)


@router.post("/validate", response_model=ValidateOut)
async def validate_agent(project_id: str, body: AgentCreate):
    errors = AgentService.validate(body.config)
    return ValidateOut(valid=not errors, errors=errors)
