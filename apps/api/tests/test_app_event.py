"""app_event polling trigger: dispatch a run per NEW item, dedup the rest."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.models import Trigger, Workflow
from forge.services.dispatch import _poll_app_event
from forge.services.runs import RunService

_WF = {
    "id": "wf_ae", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:ok"}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}

_PAYLOAD = {"items": [{"id": "1", "msg": "first"}, {"id": "2", "msg": "second"}]}


async def test_app_event_dispatches_new_then_dedupes(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_PAYLOAD)))
    monkeypatch.setattr("forge.util.http.shared_async_client", lambda: client)

    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_ae", project_id="p_ae", name="AE", executable=_WF, status="active")
        s.add(wf)
        await s.flush()
        trig = Trigger(
            tenant_id="t_ae", project_id="p_ae", workflow_id=wf.id, node_id="evt", kind="app_event",
            # last_fired_at in the past => not the baseline poll, so items dispatch
            last_fired_at=datetime.utcnow() - timedelta(minutes=10),
            config={"poll_url": "https://api.example.com/events", "items_path": "items",
                    "dedupe_key": "id", "message_path": "msg", "interval_minutes": 1},
            meta={},
        )
        s.add(trig)
        await s.commit()
        await s.refresh(trig)
        tid = trig.id

    rs = RunService(checkpointer=InMemorySaver())

    # first poll: both items are new -> 2 dispatched
    async with SessionLocal() as s:
        t = await s.get(Trigger, tid)
        n1 = await _poll_app_event(rs, t)
    assert n1 == 2

    # second poll: same items -> deduped -> 0 dispatched
    async with SessionLocal() as s:
        t = await s.get(Trigger, tid)
        assert set(t.meta["seen"]) == {"1", "2"}  # cursor persisted
        n2 = await _poll_app_event(rs, t)
    assert n2 == 0

    await client.aclose()
