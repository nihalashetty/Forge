"""Conversation-centric read model over traces for the Traces explorer.

One `Thread` = one chat session; each turn is a `Trace` carrying the denormalized
`actor` (user name / "System"), `source`, and the user_message/ai_response transcript.
This service groups traces by thread into conversations, exposes the turns of a
conversation, the filter facets, and a manual retention purge.

Grouping is done in Python (not a SQL GROUP BY) so it stays identical on SQLite (dev)
and Postgres (prod); for B2B volumes the bounded scan window is plenty. If a project
ever outgrows it, this is the one place to swap in a windowed SQL aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from forge.config import settings
from forge.models import Span, Trace


@dataclass
class Conversation:
    thread_id: str
    actor: str
    source: str
    end_user_id: str | None
    workflow_id: str | None
    turns: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    started_at: datetime | None = None
    last_activity: datetime | None = None
    status: str = "done"  # "error" if any turn errored
    preview: str = ""
    _errored: bool = field(default=False, repr=False)


def summarize(key: str, traces: list[Trace]) -> Conversation:
    """Fold a thread's turns (oldest first) into one conversation summary."""
    ordered = sorted(traces, key=lambda x: (x.started_at or x.created_at or datetime.min))
    first = ordered[0]
    c = Conversation(
        thread_id=key, actor=first.actor or "System", source=first.source or "",
        end_user_id=first.end_user_id, workflow_id=first.workflow_id, started_at=first.started_at,
    )
    for t in ordered:
        c.turns += 1
        c.total_tokens += t.total_tokens or 0
        c.total_cost_usd += t.total_cost_usd or 0.0
        # Latest actor/source/identity wins (a thread's identity can only sharpen over turns).
        c.actor = t.actor or c.actor
        c.source = t.source or c.source
        c.end_user_id = t.end_user_id or c.end_user_id
        act = t.ended_at or t.started_at or t.created_at
        if act and (c.last_activity is None or act > c.last_activity):
            c.last_activity = act
        if not c.preview and t.user_message:
            c.preview = t.user_message
        if t.status == "error" or t.error:
            c._errored = True
    c.total_cost_usd = round(c.total_cost_usd, 6)
    c.status = "error" if c._errored else "done"
    return c


class ConversationService:
    @staticmethod
    async def _scan(session: AsyncSession, tenant_id: str, project_id: str, *,
                    actor: str | None, source: str | None) -> list[Trace]:
        # Defer `ai_response` (the largest TEXT column) - the list view only needs aggregates
        # and the first user_message for the preview, so there's no reason to pull every
        # response body across the whole scan window. `summarize` never touches it.
        stmt = select(Trace).options(defer(Trace.ai_response)).where(
            Trace.tenant_id == tenant_id, Trace.project_id == project_id
        )
        if actor:
            stmt = stmt.where(Trace.actor == actor)
        if source:
            stmt = stmt.where(Trace.source == source)
        stmt = stmt.order_by(Trace.started_at.desc()).limit(settings.conversation_scan_limit)
        return list((await session.execute(stmt)).scalars())

    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str, *,
                   actor: str | None = None, source: str | None = None, status: str | None = None,
                   limit: int = 100, offset: int = 0) -> list[Conversation]:
        """Conversations (grouped by thread), newest activity first. `status` filters the
        WHOLE conversation: 'error' = any turn errored, 'success'/'done' = none errored."""
        traces = await ConversationService._scan(session, tenant_id, project_id, actor=actor, source=source)
        groups: dict[str, list[Trace]] = {}
        for t in traces:
            groups.setdefault(t.thread_id or f"trace:{t.id}", []).append(t)
        items = [summarize(key, ts) for key, ts in groups.items()]
        if status in ("error", "success", "done"):
            want_error = status == "error"
            items = [c for c in items if (c.status == "error") == want_error]
        items.sort(key=lambda c: (c.last_activity or datetime.min), reverse=True)
        return items[offset: offset + limit]

    @staticmethod
    async def turns(session: AsyncSession, tenant_id: str, project_id: str, thread_id: str) -> list[Trace]:
        """The turns (traces) of one conversation, oldest first."""
        stmt = select(Trace).where(Trace.tenant_id == tenant_id, Trace.project_id == project_id)
        if thread_id.startswith("trace:"):
            stmt = stmt.where(Trace.id == thread_id[len("trace:"):])
        else:
            stmt = stmt.where(Trace.thread_id == thread_id)
        stmt = stmt.order_by(Trace.started_at.asc())
        return list((await session.execute(stmt)).scalars())

    @staticmethod
    async def facets(session: AsyncSession, tenant_id: str, project_id: str) -> dict[str, list[str]]:
        """Distinct actors + sources for the filter controls."""
        actors = (await session.execute(
            select(Trace.actor).where(Trace.tenant_id == tenant_id, Trace.project_id == project_id).distinct()
        )).scalars()
        sources = (await session.execute(
            select(Trace.source).where(Trace.tenant_id == tenant_id, Trace.project_id == project_id).distinct()
        )).scalars()
        return {
            "actors": sorted({a for a in actors if a}),
            "sources": sorted({s for s in sources if s}),
        }

    @staticmethod
    async def purge_older_than(session: AsyncSession, tenant_id: str, project_id: str, days: int) -> int:
        """Delete traces (and their spans) older than `days`. Returns the number of traces
        removed. Manual retention control — nothing is auto-deleted."""
        cutoff = datetime.utcnow() - timedelta(days=max(0, days))
        old = (await session.execute(
            select(Trace.id).where(
                Trace.tenant_id == tenant_id, Trace.project_id == project_id, Trace.started_at < cutoff,
            )
        )).scalars().all()
        if not old:
            return 0
        await session.execute(delete(Span).where(Span.tenant_id == tenant_id, Span.trace_id.in_(old)))
        await session.execute(delete(Trace).where(Trace.tenant_id == tenant_id, Trace.id.in_(old)))
        await session.commit()
        return len(old)
