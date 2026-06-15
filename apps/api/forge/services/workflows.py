"""Workflow CRUD + validation. (Canvas->executable translation lands in Phase 4.)"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models import Run, Span, Thread, Trace, Trigger, Workflow
from forge.services.validation import ValidationResult, validate_workflow


class WorkflowService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str) -> list[Workflow]:
        rows = await session.execute(
            select(Workflow).where(
                Workflow.tenant_id == tenant_id, Workflow.project_id == project_id
            )
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, workflow_id: str) -> Workflow | None:
        row = await session.execute(
            select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.id == workflow_id)
        )
        return row.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession,
        tenant_id: str,
        project_id: str,
        *,
        name: str,
        description: str | None = None,
        executable: dict | None = None,
        canvas: dict | None = None,
    ) -> Workflow:
        wf = Workflow(
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            description=description,
            executable=executable or {},
            canvas=canvas or {},
        )
        session.add(wf)
        await session.commit()
        await session.refresh(wf)
        if wf.executable:
            await WorkflowService._sync_triggers(session, wf)
        return wf

    @staticmethod
    async def delete(session: AsyncSession, wf: Workflow) -> None:
        """Delete a workflow and its execution history (threads, runs, traces, spans).

        Runs/threads/traces reference workflow_id by value (no hard FK), so we clean
        them up explicitly to avoid orphan rows polluting the dashboard and Traces.
        """
        trace_ids = (
            await session.execute(select(Trace.id).where(Trace.workflow_id == wf.id))
        ).scalars().all()
        if trace_ids:
            await session.execute(sa_delete(Span).where(Span.trace_id.in_(trace_ids)))
        await session.execute(sa_delete(Trace).where(Trace.workflow_id == wf.id))
        await session.execute(sa_delete(Run).where(Run.workflow_id == wf.id))
        await session.execute(sa_delete(Thread).where(Thread.workflow_id == wf.id))
        await session.execute(sa_delete(Trigger).where(Trigger.workflow_id == wf.id))
        await session.delete(wf)
        await session.commit()

    @staticmethod
    async def update(
        session: AsyncSession,
        wf: Workflow,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Workflow:
        if name is not None:
            wf.name = name
        if description is not None:
            wf.description = description
        await session.commit()
        await session.refresh(wf)
        return wf

    @staticmethod
    def validate(executable: dict) -> ValidationResult:
        return validate_workflow(executable)

    @staticmethod
    async def update_executable(
        session: AsyncSession, wf: Workflow, executable: dict, *, require_valid: bool = True
    ) -> ValidationResult:
        result = validate_workflow(executable)
        if result.valid or not require_valid:
            wf.executable = executable
            await session.commit()
            await session.refresh(wf)
            await WorkflowService._sync_triggers(session, wf)
        return result

    @staticmethod
    async def save_canvas(
        session: AsyncSession, wf: Workflow, canvas: dict, executable: dict
    ) -> ValidationResult:
        """Persist the canvas (UI-owned) + compiled executable, and validate.

        Both are stored regardless of validity so the builder round-trips WIP work;
        the returned result lets the UI surface problems and gate Run/Publish.
        """
        result = validate_workflow(executable)
        wf.canvas = canvas
        wf.executable = executable
        await session.commit()
        await session.refresh(wf)
        await WorkflowService._sync_triggers(session, wf)
        return result

    @staticmethod
    async def _sync_triggers(session: AsyncSession, wf: Workflow) -> None:
        """Mirror the workflow's trigger nodes into Trigger rows (best-effort)."""
        try:
            from forge.services.triggers import TriggerService

            await TriggerService.sync_from_workflow(session, wf)
        except Exception:  # noqa: BLE001 - trigger sync must not block saving a workflow
            pass
