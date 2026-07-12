"""Project CRUD."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.knowledge.embeddings import KNOWN_EMBEDDING_DIMS
from forge.knowledge.store import ChromaStore
from forge.models import (
    Agent,
    AuthProvider,
    Channel,
    Component,
    Dataset,
    HandoffRequest,
    KbSource,
    McpClient,
    Memory,
    Project,
    QaPair,
    Run,
    Secret,
    Span,
    Thread,
    Tool,
    Trace,
    Trigger,
    Workflow,
)

# Sidebar badge counts. Keys match `countKey` in apps/web/lib/data.ts (PROJECT_NAV); the
# sidebar reads these instead of fetching six full lists just to call `.length`.
_COUNT_MODELS: dict[str, type] = {
    "workflows": Workflow,
    "agents": Agent,
    "tools": Tool,
    "components": Component,
    "knowledge": KbSource,
    "auth": AuthProvider,
}


class ProjectService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str) -> list[Project]:
        rows = await session.execute(
            select(Project).where(Project.tenant_id == tenant_id, Project.archived.is_(False))
        )
        return list(rows.scalars())

    @staticmethod
    async def counts(session: AsyncSession, tenant_id: str, project_id: str) -> dict[str, int]:
        """Per-resource counts for the project sidebar badges. Cheap COUNT(*) per table
        (tenant+project scoped) instead of pulling six full lists client-side."""
        out: dict[str, int] = {}
        for key, model in _COUNT_MODELS.items():
            n = await session.scalar(
                select(func.count()).select_from(model).where(
                    model.tenant_id == tenant_id, model.project_id == project_id
                )
            )
            out[key] = int(n or 0)
        return out

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, project_id: str) -> Project | None:
        row = await session.execute(
            select(Project).where(Project.tenant_id == tenant_id, Project.id == project_id)
        )
        return row.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession,
        tenant_id: str,
        *,
        name: str,
        slug: str | None = None,
        description: str | None = None,
        config: dict | None = None,
    ) -> Project:
        project = Project(
            tenant_id=tenant_id,
            name=name,
            slug=slug or name.lower().replace(" ", "-"),
            description=description,
            config=config or {},
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project

    @staticmethod
    async def update(
        session: AsyncSession, project: Project, *, name: str | None = None,
        description: str | None = None, config: dict | None = None,
    ) -> Project:
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if config is not None:
            project.config = config
        await session.commit()
        await session.refresh(project)
        return project

    @staticmethod
    async def delete(session: AsyncSession, project: Project, *, checkpointer: Any = None) -> None:
        """Delete a project and all project-scoped runtime/build artifacts."""
        lg_thread_ids = (
            await session.execute(
                select(Thread.lg_thread_id).where(
                    Thread.tenant_id == project.tenant_id,
                    Thread.project_id == project.id,
                )
            )
        ).scalars().all()

        trace_ids = (
            await session.execute(
                select(Trace.id).where(
                    Trace.tenant_id == project.tenant_id,
                    Trace.project_id == project.id,
                )
            )
        ).scalars().all()
        if trace_ids:
            await session.execute(sa_delete(Span).where(Span.trace_id.in_(trace_ids)))

        # Clear vectors across all dim-keyed collections (docs, Q&A, memory, cache),
        # scoped to this tenant+project by metadata.
        where = {"$and": [{"tenant_id": {"$eq": project.tenant_id}}, {"project_id": {"$eq": project.id}}]}
        # Sweep every known embedding dim (single source of truth) - a hardcoded subset that
        # omitted the 384-dim default left a deleted project's vectors orphaned in Chroma.
        for dim in sorted(KNOWN_EMBEDDING_DIMS):
            for prefix in ("forge_kb", "forge_qa", "forge_mem", "forge_cache"):
                try:
                    ChromaStore(collection=f"{prefix}_{dim}")._col.delete(where=where)
                except Exception:  # noqa: BLE001 - best-effort vector cleanup
                    pass

        if checkpointer is not None:
            for thread_id in lg_thread_ids:
                try:
                    if hasattr(checkpointer, "adelete_thread"):
                        await checkpointer.adelete_thread(thread_id)
                    elif hasattr(checkpointer, "delete_thread"):
                        checkpointer.delete_thread(thread_id)
                except Exception:  # noqa: BLE001 - database rows below make runs unreachable
                    pass

        scoped_models = [
            Trace,
            Run,
            Thread,
            Workflow,
            Agent,
            Tool,
            AuthProvider,
            Secret,
            KbSource,
            QaPair,
            McpClient,
            Channel,
            Trigger,
            Dataset,
            Memory,
            HandoffRequest,
        ]
        for model in scoped_models:
            await session.execute(
                sa_delete(model).where(
                    model.tenant_id == project.tenant_id,
                    model.project_id == project.id,
                )
            )
        await session.delete(project)
        await session.commit()
