"""Agent preset CRUD. Config validates against the agent node schema (forge/nodes/agent)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models import Agent
from forge.schemas.contracts import validate_against_id


class AgentService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str) -> list[Agent]:
        rows = await session.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.project_id == project_id)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, agent_id: str) -> Agent | None:
        row = await session.execute(select(Agent).where(Agent.tenant_id == tenant_id, Agent.id == agent_id))
        return row.scalar_one_or_none()

    @staticmethod
    def validate(config: dict) -> list[dict]:
        return validate_against_id(config, "forge/nodes/agent")

    @staticmethod
    async def create(session: AsyncSession, tenant_id: str, project_id: str, *, name: str, config: dict,
                     created_by: str | None = None, created_by_email: str | None = None) -> Agent:
        agent = Agent(tenant_id=tenant_id, project_id=project_id, name=name, config=config or {},
                      created_by=created_by, created_by_email=created_by_email)
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent

    @staticmethod
    async def delete(session: AsyncSession, agent: Agent) -> None:
        await session.delete(agent)
        await session.commit()

    @staticmethod
    async def update(session: AsyncSession, agent: Agent, *, name: str | None = None, config: dict | None = None) -> Agent:
        if name is not None:
            agent.name = name
        if config is not None:
            agent.config = config
            agent.version += 1
        await session.commit()
        await session.refresh(agent)
        return agent
