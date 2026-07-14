"""Agent preset endpoints (CRUD + validate)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.schemas.dto import AgentCreate, AgentOut, AgentUpdate, ValidateOut
from forge.services.agents import AgentService

router = APIRouter(prefix="/v1/projects/{project_id}/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await AgentService.list(session, tenant_id, project_id)


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(project_id: str, body: AgentCreate, session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id), user: CurrentUser = Depends(require_role("editor"))):
    return await AgentService.create(session, tenant_id, project_id, name=body.name, config=body.config,
                                     created_by=user.id, created_by_email=user.email)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(project_id: str, agent_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    agent = await AgentService.get(session, tenant_id, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(project_id: str, agent_id: str, body: AgentUpdate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id),
                       _: CurrentUser = Depends(require_role("editor"))):
    agent = await AgentService.get(session, tenant_id, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return await AgentService.update(session, agent, name=body.name, config=body.config)


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
