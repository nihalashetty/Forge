"""Project lifecycle tests."""

from __future__ import annotations

from sqlalchemy import func, select

from forge.db.base import SessionLocal
from forge.models import Agent, AuthProvider, KbSource, McpClient, Project, QaPair, Run, Secret, Span, Thread, Tool, Trace, Workflow
from forge.services.projects import ProjectService


async def _count(session, model, **where) -> int:
    stmt = select(func.count()).select_from(model)
    for key, value in where.items():
        stmt = stmt.where(getattr(model, key) == value)
    return int((await session.execute(stmt)).scalar_one())


async def test_delete_project_removes_project_scoped_data_and_trace_spans():
    tenant_id = "tenant_delete_project"
    deleted_threads: list[str] = []

    class FakeCheckpointer:
        async def adelete_thread(self, thread_id: str) -> None:
            deleted_threads.append(thread_id)

    async with SessionLocal() as session:
        project = await ProjectService.create(session, tenant_id, name="Delete Me", slug="delete-me")
        workflow = Workflow(tenant_id=tenant_id, project_id=project.id, name="wf")
        thread = Thread(tenant_id=tenant_id, project_id=project.id, workflow_id="wf1", lg_thread_id="lg1")
        run = Run(tenant_id=tenant_id, project_id=project.id, workflow_id="wf1", thread_id="thread1")
        trace = Trace(tenant_id=tenant_id, project_id=project.id, workflow_id="wf1", run_id="run1", name="trace")
        session.add_all([workflow, thread, run, trace])
        await session.flush()
        session.add_all([
            Span(tenant_id=tenant_id, trace_id=trace.id, name="span", kind="node"),
            Agent(tenant_id=tenant_id, project_id=project.id, name="agent", config={}),
            Tool(tenant_id=tenant_id, project_id=project.id, name="tool", kind="builtin", config={}),
            AuthProvider(tenant_id=tenant_id, project_id=project.id, name="auth", kind="bearer", config={}),
            Secret(tenant_id=tenant_id, project_id=project.id, name="secret", kind="api_key", encrypted_value=b"x"),
            KbSource(tenant_id=tenant_id, project_id=project.id, kind="text", name="source"),
            QaPair(tenant_id=tenant_id, project_id=project.id, question="q", answer="a"),
            McpClient(tenant_id=tenant_id, project_id=project.id, name="mcp"),
        ])
        await session.commit()

        await ProjectService.delete(session, project, checkpointer=FakeCheckpointer())

        assert await ProjectService.get(session, tenant_id, project.id) is None
        for model in (Workflow, Thread, Run, Trace, Agent, Tool, AuthProvider, Secret, KbSource, QaPair, McpClient):
            assert await _count(session, model, project_id=project.id) == 0
        assert await _count(session, Span, trace_id=trace.id) == 0
        assert deleted_threads == ["lg1"]
