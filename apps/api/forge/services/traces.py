"""Trace + span read access for the Traces explorer."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models import Span, Trace


class TraceService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str, limit: int = 100) -> list[Trace]:
        rows = await session.execute(
            select(Trace).where(Trace.tenant_id == tenant_id, Trace.project_id == project_id)
            .order_by(Trace.started_at.desc()).limit(limit)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, trace_id: str) -> Trace | None:
        row = await session.execute(select(Trace).where(Trace.tenant_id == tenant_id, Trace.id == trace_id))
        return row.scalar_one_or_none()

    @staticmethod
    async def spans(session: AsyncSession, tenant_id: str, trace_id: str) -> list[Span]:
        rows = await session.execute(
            select(Span).where(Span.tenant_id == tenant_id, Span.trace_id == trace_id).order_by(Span.created_at.asc())
        )
        return list(rows.scalars())
