"""Durable SSE (finding #12): run execution is decoupled from the client connection.

A mid-run client disconnect must NOT end the run - the graph keeps executing in a detached
background task, and a client can reattach (same run_id) to get the final answer. Frames carry
a monotonic id so a `Last-Event-ID` reconnect replays only what was missed.
"""

from __future__ import annotations

import uuid

from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.models import Run, Thread, Workflow
from forge.services.runs import RunService

_ANSWER = "Hello from the durable run."
_WF = {
    "id": "wf_sse", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": f"fake:{_ANSWER}", "tools": []}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}


async def _seed_queued_run() -> tuple[str, str, str]:
    """Insert a workflow + thread + a queued run directly; returns (tenant, project, run_id)."""
    t, p = f"t_{uuid.uuid4().hex[:8]}", f"p_{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as s:
        wf = Workflow(tenant_id=t, project_id=p, name="w", executable=_WF, status="active")
        s.add(wf)
        await s.flush()
        thread = Thread(tenant_id=t, project_id=p, workflow_id=wf.id, lg_thread_id=f"lg_{uuid.uuid4().hex}", meta={})
        s.add(thread)
        await s.flush()
        run = Run(tenant_id=t, project_id=p, workflow_id=wf.id, thread_id=thread.id, status="queued",
                  input={"messages": [{"role": "user", "content": "hi"}]})
        s.add(run)
        await s.commit()
        return t, p, run.id


async def test_disconnect_leaves_run_running_and_reattach_gets_answer():
    t, p, rid = await _seed_queued_run()
    rs = RunService(checkpointer=InMemorySaver())

    # First connection: take a single frame, then "disconnect" by closing the subscriber.
    agen = rs.stream(run_id=rid, tenant_id=t, project_id=p)
    first = await agen.__anext__()
    assert first["event"] == "run" and first["id"] == "1"
    await agen.aclose()  # client gone - the detached executor must keep running

    # Reattach from where we left off; the run completes and we get the final answer.
    frames = [f async for f in rs.stream(run_id=rid, tenant_id=t, project_id=p, last_event_id=int(first["id"]))]
    done = [f for f in frames if f["event"] == "done"]
    assert done, [f["event"] for f in frames]
    assert _ANSWER in (done[-1]["data"].get("answer") or "")

    # The disconnect did NOT cancel the run - it ran to completion.
    async with SessionLocal() as s:
        run = await s.get(Run, rid)
        assert run.status == "done", run.status


async def test_last_event_id_replays_only_later_frames():
    t, p, rid = await _seed_queued_run()
    rs = RunService(checkpointer=InMemorySaver())

    # Drive the run to completion on the first connection, recording every frame id.
    frames = [f async for f in rs.stream(run_id=rid, tenant_id=t, project_id=p)]
    ids = [int(f["id"]) for f in frames if f.get("id")]
    assert ids == sorted(ids) and ids[0] == 1  # monotonic, starting at 1
    assert any(f["event"] == "done" for f in frames)

    # Reattach with a mid-stream Last-Event-ID: only strictly-later frames are replayed.
    cutoff = ids[len(ids) // 2]
    replayed = [f async for f in rs.stream(run_id=rid, tenant_id=t, project_id=p, last_event_id=cutoff)]
    assert replayed, "reattach after completion should replay the retained tail"
    assert all(int(f["id"]) > cutoff for f in replayed)
    # The retained tail still includes the terminal done frame.
    assert any(f["event"] == "done" for f in replayed)
