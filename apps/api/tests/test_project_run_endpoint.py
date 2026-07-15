"""The single project-level run endpoint (POST /v1/projects/{id}/run).

One generic surface over the existing run machinery: it runs the project's *configured*
workflow (config.api_workflow_id), takes `stream` as the only per-request knob, and routes a
`resume` body to the HITL machinery. These tests drive it in-process over ASGI with a fake
model, so no real LLM is called.
"""

from __future__ import annotations

import uuid

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from forge.main import create_app

# A trivial one-agent workflow whose fake model always answers with this exact text - lets us
# assert the endpoint actually ran the configured workflow end to end.
_ANSWER = "Hello from Forge."
_WF = {
    "id": "wf_run_ep", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": f"fake:{_ANSWER}", "tools": []}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}


def _client() -> httpx.AsyncClient:
    app = create_app()
    # No app lifespan runs in-process, so hand the run service a checkpointer directly
    # (aget_state needs one); prod gets this from the lifespan.
    app.state.checkpointer = InMemorySaver()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _email() -> str:
    return f"u{uuid.uuid4().hex[:10]}@example.com"


async def _project_with_configured_workflow(c: httpx.AsyncClient) -> tuple[dict, str]:
    """Register an owner, create a project + workflow, and pin the workflow as the project's
    API workflow. Returns (auth header, project_id)."""
    reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
    h = {"Authorization": f"Bearer {reg['access_token']}"}
    pid = (await c.post("/v1/projects", json={"name": "Run API Project"}, headers=h)).json()["id"]
    wid = (await c.post(f"/v1/projects/{pid}/workflows", json={"name": "Chat", "executable": _WF}, headers=h)).json()["id"]
    # The saved project setting that designates which workflow the /run endpoint executes.
    r = await c.patch(f"/v1/projects/{pid}", json={"config": {"api_workflow_id": wid}}, headers=h)
    assert r.status_code == 200, r.text
    return h, pid


async def test_run_non_stream_returns_answer_and_thread():
    async with _client() as c:
        h, pid = await _project_with_configured_workflow(c)
        r = await c.post(
            f"/v1/projects/{pid}/run",
            json={"input": {"messages": [{"role": "user", "content": "hi"}]}, "stream": False},
            headers=h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert _ANSWER in (body.get("answer") or ""), body
        assert body.get("thread_id"), body
        assert body.get("status") == "done", body


async def test_run_stream_emits_ready_and_done_frames():
    async with _client() as c:
        h, pid = await _project_with_configured_workflow(c)
        r = await c.post(
            f"/v1/projects/{pid}/run",
            json={"input": {"messages": [{"role": "user", "content": "hi"}]}, "stream": True},
            headers=h,
        )
        assert r.status_code == 200, r.text
        text = r.text
        # A leading `ready` frame hands the caller the canonical thread_id, and the run
        # finishes with a `done` frame carrying the answer.
        assert "event: ready" in text, text
        assert '"thread_id"' in text
        assert "event: done" in text, text
        assert _ANSWER in text


async def test_thread_id_from_run_continues_conversation():
    async with _client() as c:
        h, pid = await _project_with_configured_workflow(c)
        first = (await c.post(
            f"/v1/projects/{pid}/run",
            json={"input": {"messages": [{"role": "user", "content": "hi"}]}, "stream": False},
            headers=h,
        )).json()
        tid = first["thread_id"]
        # Reusing the thread_id must be accepted (the checkpointer holds the history).
        r = await c.post(
            f"/v1/projects/{pid}/run",
            json={"thread_id": tid, "input": {"messages": [{"role": "user", "content": "again"}]}, "stream": False},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("thread_id") == tid


async def test_stream_exposes_one_consistent_thread_id_that_continues():
    """Regression (shared chat memory): the streaming path once handed the caller TWO different
    thread handles - the DB Thread.id in the `ready` frame and the composite LangGraph id
    (`{tenant}:{uuid}`) in the `run` frame. A caller that stored the `run` frame's id echoed a
    handle that never matched Thread.id, so every turn spun up a fresh thread and the agent
    "forgot" the conversation. Every thread_id in the stream must now be identical and reusable."""
    import re

    async with _client() as c:
        h, pid = await _project_with_configured_workflow(c)
        r = await c.post(
            f"/v1/projects/{pid}/run",
            json={"input": {"messages": [{"role": "user", "content": "hi"}]}, "stream": True},
            headers=h,
        )
        assert r.status_code == 200, r.text
        tids = re.findall(r'"thread_id":\s*"([^"]+)"', r.text)
        assert tids, r.text
        assert len(set(tids)) == 1, f"stream exposed conflicting thread handles: {set(tids)}"
        # The handle from the stream must reattach to the same thread (checkpointer holds history).
        again = (await c.post(
            f"/v1/projects/{pid}/run",
            json={"thread_id": tids[0], "input": {"messages": [{"role": "user", "content": "again"}]}, "stream": False},
            headers=h,
        )).json()
        assert again.get("thread_id") == tids[0]


async def test_project_without_a_workflow_is_404():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        pid = (await c.post("/v1/projects", json={"name": "Empty"}, headers=h)).json()["id"]
        r = await c.post(f"/v1/projects/{pid}/run", json={"input": {}, "stream": False}, headers=h)
        assert r.status_code == 404, r.text


async def test_resume_requires_thread_id():
    async with _client() as c:
        h, pid = await _project_with_configured_workflow(c)
        r = await c.post(f"/v1/projects/{pid}/run", json={"resume": {"value": "approve"}}, headers=h)
        assert r.status_code == 400, r.text


async def test_resume_with_no_interrupted_run_is_409():
    async with _client() as c:
        h, pid = await _project_with_configured_workflow(c)
        r = await c.post(
            f"/v1/projects/{pid}/run",
            json={"thread_id": "does-not-exist", "resume": {"value": "approve"}},
            headers=h,
        )
        assert r.status_code == 409, r.text
