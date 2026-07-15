"""Tool Set CRUD + membership (many-to-many Tool <-> ToolSet).

A tool set is a describable group of tools. Sets organize the Tools screen (they render as
folders), can be granted to an agent as a unit (agent config.toolsets), and can be published
as a GitHub-style toolset over MCP. Membership is many-to-many via ToolSetMember, so a tool
may belong to several sets. Membership is always validated to real tools in the same project.
"""

from __future__ import annotations

import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models import Tool, ToolSet, ToolSetMember


def _slugify(name: str) -> str:
    """url-safe slug from a display name (matches the simple project-slug convention but
    tolerant of punctuation): lowercase, non-alnum runs -> '-', trimmed."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "set"


class ToolSetService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str) -> list[ToolSet]:
        rows = await session.execute(
            select(ToolSet)
            .where(ToolSet.tenant_id == tenant_id, ToolSet.project_id == project_id)
            .order_by(ToolSet.name)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, set_id: str) -> ToolSet | None:
        row = await session.execute(
            select(ToolSet).where(ToolSet.tenant_id == tenant_id, ToolSet.id == set_id)
        )
        return row.scalar_one_or_none()

    @staticmethod
    async def members_map(session: AsyncSession, tenant_id: str, project_id: str) -> dict[str, list[str]]:
        """{tool_set_id: [tool_id, ...]} for every set in the project, in one query."""
        rows = await session.execute(
            select(ToolSetMember.tool_set_id, ToolSetMember.tool_id)
            .where(ToolSetMember.tenant_id == tenant_id, ToolSetMember.project_id == project_id)
            .order_by(ToolSetMember.created_at)
        )
        out: dict[str, list[str]] = {}
        for set_id, tool_id in rows.all():
            out.setdefault(set_id, []).append(tool_id)
        return out

    @staticmethod
    async def member_ids(session: AsyncSession, tenant_id: str, set_id: str) -> list[str]:
        rows = await session.execute(
            select(ToolSetMember.tool_id)
            .where(ToolSetMember.tenant_id == tenant_id, ToolSetMember.tool_set_id == set_id)
            .order_by(ToolSetMember.created_at)
        )
        return [r[0] for r in rows.all()]

    @staticmethod
    async def tool_ids_for_sets(session: AsyncSession, tenant_id: str, project_id: str, set_ids: list[str]) -> list[str]:
        """Union of member tool ids across the given sets, order-stable and de-duplicated.
        Used to resolve an agent's config.toolsets into concrete tool ids at compile time."""
        if not set_ids:
            return []
        rows = await session.execute(
            select(ToolSetMember.tool_id)
            .where(
                ToolSetMember.tenant_id == tenant_id,
                ToolSetMember.project_id == project_id,
                ToolSetMember.tool_set_id.in_(list(set_ids)),
            )
            .order_by(ToolSetMember.created_at)
        )
        seen: set[str] = set()
        out: list[str] = []
        for (tool_id,) in rows.all():
            if tool_id not in seen:
                out.append(tool_id)
                seen.add(tool_id)
        return out

    @staticmethod
    async def _unique_slug(session: AsyncSession, tenant_id: str, project_id: str, base: str, *, current_slug: str | None = None) -> str:
        rows = await session.execute(
            select(ToolSet.slug).where(ToolSet.tenant_id == tenant_id, ToolSet.project_id == project_id)
        )
        taken = {r[0] for r in rows.all() if r[0]}
        taken.discard(current_slug)  # a set may keep its own slug
        slug = base
        i = 2
        while slug in taken:
            slug = f"{base}-{i}"
            i += 1
        return slug

    @staticmethod
    async def _valid_tool_ids(session: AsyncSession, tenant_id: str, project_id: str, tool_ids: list[str]) -> list[str]:
        """Filter to tool ids that are real tools in this project; preserve order, de-dupe."""
        if not tool_ids:
            return []
        rows = await session.execute(
            select(Tool.id).where(
                Tool.tenant_id == tenant_id, Tool.project_id == project_id, Tool.id.in_(list(tool_ids))
            )
        )
        found = {r[0] for r in rows.all()}
        seen: set[str] = set()
        out: list[str] = []
        for tid in tool_ids:
            if tid in found and tid not in seen:
                out.append(tid)
                seen.add(tid)
        return out

    @staticmethod
    async def _replace_members(session: AsyncSession, tenant_id: str, project_id: str, set_id: str, tool_ids: list[str]) -> None:
        valid = await ToolSetService._valid_tool_ids(session, tenant_id, project_id, tool_ids)
        await session.execute(delete(ToolSetMember).where(ToolSetMember.tool_set_id == set_id))
        for tid in valid:
            session.add(ToolSetMember(tenant_id=tenant_id, project_id=project_id, tool_set_id=set_id, tool_id=tid))

    @staticmethod
    async def create(session: AsyncSession, tenant_id: str, project_id: str, *, name: str, description: str = "",
                     icon: str | None = None, is_default: bool = False, exposed: bool = True,
                     tool_ids: list[str] | None = None) -> ToolSet:
        slug = await ToolSetService._unique_slug(session, tenant_id, project_id, _slugify(name))
        ts = ToolSet(
            tenant_id=tenant_id, project_id=project_id, name=name, slug=slug,
            description=description or "", icon=icon or None, is_default=bool(is_default), exposed=bool(exposed),
        )
        session.add(ts)
        await session.flush()  # populate ts.id before inserting membership rows
        if tool_ids:
            await ToolSetService._replace_members(session, tenant_id, project_id, ts.id, tool_ids)
        await session.commit()
        await session.refresh(ts)
        return ts

    @staticmethod
    async def update(session: AsyncSession, ts: ToolSet, *, name: str | None = None, description: str | None = None,
                     icon: str | None = None, is_default: bool | None = None, exposed: bool | None = None,
                     tool_ids: list[str] | None = None) -> ToolSet:
        if name is not None and name != ts.name:
            ts.name = name
            ts.slug = await ToolSetService._unique_slug(session, ts.tenant_id, ts.project_id, _slugify(name), current_slug=ts.slug)
        if description is not None:
            ts.description = description
        if icon is not None:
            ts.icon = icon or None
        if is_default is not None:
            ts.is_default = bool(is_default)
        if exposed is not None:
            ts.exposed = bool(exposed)
        if tool_ids is not None:
            await ToolSetService._replace_members(session, ts.tenant_id, ts.project_id, ts.id, tool_ids)
        await session.commit()
        await session.refresh(ts)
        return ts

    @staticmethod
    async def delete(session: AsyncSession, ts: ToolSet) -> None:
        await session.execute(delete(ToolSetMember).where(ToolSetMember.tool_set_id == ts.id))
        await session.delete(ts)
        await session.commit()

    @staticmethod
    async def add_member(session: AsyncSession, ts: ToolSet, tool_id: str) -> None:
        if not await ToolSetService._valid_tool_ids(session, ts.tenant_id, ts.project_id, [tool_id]):
            return  # ignore unknown / cross-project tool ids
        existing = await session.execute(
            select(ToolSetMember.id).where(ToolSetMember.tool_set_id == ts.id, ToolSetMember.tool_id == tool_id)
        )
        if existing.scalar_one_or_none() is None:
            session.add(ToolSetMember(tenant_id=ts.tenant_id, project_id=ts.project_id, tool_set_id=ts.id, tool_id=tool_id))
            await session.commit()

    @staticmethod
    async def remove_member(session: AsyncSession, ts: ToolSet, tool_id: str) -> None:
        await session.execute(
            delete(ToolSetMember).where(ToolSetMember.tool_set_id == ts.id, ToolSetMember.tool_id == tool_id)
        )
        await session.commit()
