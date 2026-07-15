"""ChannelService - CRUD for deployment surfaces + inbound routing.

A Channel binds a project workflow to an email surface. Inbound events look the
channel up by its public `key`, resolve the workflow, dispatch a run, and the
channel-specific adapter sends the answer back.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from forge.models import Channel, Workflow

CHANNEL_TYPES = ("email",)


class ChannelService:
    @staticmethod
    async def list(session, tenant_id: str, project_id: str) -> list[Channel]:
        rows = await session.execute(
            select(Channel).where(Channel.tenant_id == tenant_id, Channel.project_id == project_id)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session, tenant_id: str, channel_id: str) -> Channel | None:
        return (await session.execute(
            select(Channel).where(Channel.tenant_id == tenant_id, Channel.id == channel_id)
        )).scalar_one_or_none()

    @staticmethod
    async def by_key(session, type_: str, key: str) -> Channel | None:
        return (await session.execute(
            select(Channel).where(Channel.type == type_, Channel.key == key, Channel.enabled.is_(True))
        )).scalar_one_or_none()

    @staticmethod
    async def create(session, tenant_id: str, project_id: str, *, type_: str, name: str,
                     workflow_id: str | None = None, config: dict | None = None) -> Channel:
        if type_ not in CHANNEL_TYPES:
            raise ValueError(f"unknown channel type {type_!r}")
        ch = Channel(
            tenant_id=tenant_id, project_id=project_id, type=type_, name=name,
            workflow_id=workflow_id, config=config or {}, key=uuid.uuid4().hex, enabled=True,
        )
        session.add(ch)
        await session.commit()
        await session.refresh(ch)
        return ch

    @staticmethod
    async def update(session, ch: Channel, *, name=None, workflow_id=None, config=None, enabled=None) -> Channel:
        if name is not None:
            ch.name = name
        if workflow_id is not None:
            ch.workflow_id = workflow_id
        if config is not None:
            ch.config = config
        if enabled is not None:
            ch.enabled = enabled
        await session.commit()
        await session.refresh(ch)
        return ch

    @staticmethod
    async def delete(session, ch: Channel) -> None:
        await session.delete(ch)
        await session.commit()

    @staticmethod
    async def resolve_workflow_id(session, ch: Channel) -> str | None:
        """The workflow that handles this channel: the explicit binding, else the
        project's first active workflow."""
        if ch.workflow_id:
            return ch.workflow_id
        wf = (await session.execute(
            select(Workflow).where(
                Workflow.tenant_id == ch.tenant_id, Workflow.project_id == ch.project_id,
                Workflow.status == "active",
            ).limit(1)
        )).scalar_one_or_none()
        return wf.id if wf else None
