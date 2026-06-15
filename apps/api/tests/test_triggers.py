"""Trigger sync + scheduling + webhook dispatch (end-to-end via services)."""

from __future__ import annotations

from datetime import datetime, timedelta

from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.models import Trigger, Workflow
from forge.services.dispatch import dispatch_trigger
from forge.services.runs import RunService
from forge.services.triggers import TriggerService

_WEBHOOK_WF = {
    "id": "wf_hook", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "hook",
    "nodes": [
        {"id": "hook", "type": "webhook_in", "config": {"message_path": "text"}},
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:Done."}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "hook", "target": "agent"}, {"source": "agent", "target": "end"}],
}


async def _make_wf(tenant="t_trig", project="p_trig") -> Workflow:
    async with SessionLocal() as s:
        wf = Workflow(tenant_id=tenant, project_id=project, name="Hooked", executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        await TriggerService.sync_from_workflow(s, wf)
        return wf


async def test_sync_creates_webhook_trigger_with_key():
    wf = await _make_wf()
    async with SessionLocal() as s:
        trigs = (await s.execute(Trigger.__table__.select().where(Trigger.workflow_id == wf.id))).fetchall()
    assert len(trigs) == 1
    async with SessionLocal() as s:
        t = await TriggerService.by_key(s, trigs[0].key)
    assert t is not None and t.kind == "webhook_in" and t.key


def test_build_input_message_path_extracts_field():
    t = Trigger(tenant_id="t", project_id="p", workflow_id="w", node_id="hook", kind="webhook_in", config={"message_path": "text"})
    assert TriggerService.build_input(t, {"text": "hello there"}) == {"messages": [{"role": "user", "content": "hello there"}]}


def test_build_input_schedule_uses_config_message():
    t = Trigger(tenant_id="t", project_id="p", workflow_id="w", node_id="s", kind="schedule", config={"message": "tick"})
    assert TriggerService.build_input(t, None)["messages"][0]["content"] == "tick"


def test_is_due_interval():
    t = Trigger(tenant_id="t", project_id="p", workflow_id="w", node_id="s", kind="schedule", config={"every_minutes": 10}, enabled=True)
    assert TriggerService.is_due(t, datetime.utcnow()) is True  # never fired -> due
    t.last_fired_at = datetime.utcnow()
    assert TriggerService.is_due(t, datetime.utcnow()) is False
    t.last_fired_at = datetime.utcnow() - timedelta(minutes=11)
    assert TriggerService.is_due(t, datetime.utcnow()) is True


async def test_dispatch_webhook_runs_workflow():
    wf = await _make_wf("t_d", "p_d")
    async with SessionLocal() as s:
        trig = (await s.execute(Trigger.__table__.select().where(Trigger.workflow_id == wf.id))).fetchone()
        trigger = await TriggerService.by_key(s, trig.key)
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_trigger(rs, trigger, {"text": "ping"})
    assert result.get("answer") == "Done." and result.get("status") == "done"
