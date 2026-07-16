"""Conversation-centric Traces view: turns grouped by session/user, filters, capture, purge."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from forge.db.base import SessionLocal
from forge.main import create_app
from forge.models import Span, Trace
from forge.services.conversations import ConversationService

TENANT = "t_conv"


def _pid() -> str:
    """A fresh project id per test — the suite shares one DB file across tests, so isolate
    each test's rows under its own project to keep assertions on the full list stable."""
    return f"p_{uuid.uuid4().hex[:10]}"


async def _add_trace(*, project, thread_id, actor, source, status="done", user="hi", ai="hello",
                     tokens=10, cost=0.001, started=None, error=None, run_id=None):
    async with SessionLocal() as s:
        t = Trace(
            tenant_id=TENANT, project_id=project, workflow_id="wf1",
            run_id=run_id or str(uuid.uuid4()), thread_id=thread_id, name="run", status=status,
            started_at=started or datetime.utcnow(), ended_at=started or datetime.utcnow(),
            latency_ms=5, total_tokens=tokens, total_cost_usd=cost,
            source=source, actor=actor, end_user_id=None,
            user_message=user, ai_response=ai, error=error,
        )
        s.add(t)
        await s.commit()
        return t.id


# --- grouping + summary --------------------------------------------------------

async def test_turns_group_into_one_conversation_per_thread():
    pid = _pid()
    await _add_trace(project=pid, thread_id="th1", actor="Alice", source="api", user="q1", ai="a1",
                     started=datetime.utcnow() - timedelta(minutes=5))
    await _add_trace(project=pid, thread_id="th1", actor="Alice", source="api", user="q2", ai="a2",
                     started=datetime.utcnow() - timedelta(minutes=4))
    await _add_trace(project=pid, thread_id="th2", actor="System", source="playground", user="test", ai="ok")

    async with SessionLocal() as s:
        convos = await ConversationService.list(s, TENANT, pid)
    by_thread = {c.thread_id: c for c in convos}
    assert set(by_thread) == {"th1", "th2"}
    assert by_thread["th1"].turns == 2 and by_thread["th1"].actor == "Alice"
    assert by_thread["th1"].total_tokens == 20
    assert by_thread["th1"].preview == "q1"          # earliest turn's user message
    assert by_thread["th2"].actor == "System" and by_thread["th2"].source == "playground"


async def test_pause_and_resume_of_one_run_count_as_one_turn():
    # A HITL pause writes an `interrupted` Trace and the resume writes a `done` Trace, both under
    # the SAME run_id (and same user_message, since run.input is unchanged). They must fold into
    # ONE turn, not two - matching the run-grouped transcript in the Traces UI.
    pid = _pid()
    rid = str(uuid.uuid4())
    await _add_trace(project=pid, thread_id="thHITL", actor="System", source="playground",
                     status="interrupted", user="approve this", ai=None, run_id=rid,
                     started=datetime.utcnow() - timedelta(minutes=2))
    await _add_trace(project=pid, thread_id="thHITL", actor="System", source="playground",
                     status="done", user="approve this", ai="done!", run_id=rid,
                     started=datetime.utcnow() - timedelta(minutes=1))
    async with SessionLocal() as s:
        convos = await ConversationService.list(s, TENANT, pid)
        turns = await ConversationService.turns(s, TENANT, pid, "thHITL")
    conv = next(c for c in convos if c.thread_id == "thHITL")
    assert conv.turns == 1, "pause + resume of one run is a single turn"
    assert conv.status != "error"  # an interrupt is not a failure
    assert len(turns) == 2  # both raw Trace segments are still returned; the UI groups by run_id


async def test_conversation_status_is_error_if_any_turn_errored():
    pid = _pid()
    await _add_trace(project=pid, thread_id="thE", actor="Bob", source="embed", status="done")
    await _add_trace(project=pid, thread_id="thE", actor="Bob", source="embed", status="error", error="boom")
    async with SessionLocal() as s:
        convos = await ConversationService.list(s, TENANT, pid)
        errs = await ConversationService.list(s, TENANT, pid, status="error")
        oks = await ConversationService.list(s, TENANT, pid, status="success")
    assert next(c for c in convos if c.thread_id == "thE").status == "error"
    assert [c.thread_id for c in errs] == ["thE"]
    assert "thE" not in [c.thread_id for c in oks]


async def test_filter_by_actor_and_source():
    pid = _pid()
    await _add_trace(project=pid, thread_id="thA", actor="Alice", source="api")
    await _add_trace(project=pid, thread_id="thS", actor="System", source="playground")
    async with SessionLocal() as s:
        alice = await ConversationService.list(s, TENANT, pid, actor="Alice")
        system = await ConversationService.list(s, TENANT, pid, source="playground")
    assert [c.thread_id for c in alice] == ["thA"]
    assert [c.thread_id for c in system] == ["thS"]


async def test_search_matches_any_turn_and_keeps_the_full_conversation():
    pid = _pid()
    await _add_trace(project=pid, thread_id="match-user", actor="Alice", source="api",
                     user="Find the quarterly invoice", ai="Here it is",
                     started=datetime.utcnow() - timedelta(minutes=3))
    await _add_trace(project=pid, thread_id="match-user", actor="Alice", source="api",
                     user="Thanks", ai="You're welcome",
                     started=datetime.utcnow() - timedelta(minutes=2))
    await _add_trace(project=pid, thread_id="match-ai", actor="Bob", source="playground",
                     user="What was the result?", ai="The needle is in this answer")
    await _add_trace(project=pid, thread_id="miss", actor="Carol", source="api",
                     user="Unrelated", ai="Nothing to see")

    async with SessionLocal() as s:
        user_match = await ConversationService.list(s, TENANT, pid, search="QUARTERLY")
        ai_match = await ConversationService.list(s, TENANT, pid, search="needle")

    assert [c.thread_id for c in user_match] == ["match-user"]
    assert user_match[0].turns == 2
    assert user_match[0].total_tokens == 20
    assert [c.thread_id for c in ai_match] == ["match-ai"]


async def test_turns_endpoint_returns_transcript_in_order():
    pid = _pid()
    await _add_trace(project=pid, thread_id="thT", actor="Al", source="api", user="first", ai="r1",
                     started=datetime.utcnow() - timedelta(minutes=2))
    await _add_trace(project=pid, thread_id="thT", actor="Al", source="api", user="second", ai="r2",
                     started=datetime.utcnow() - timedelta(minutes=1))
    async with SessionLocal() as s:
        turns = await ConversationService.turns(s, TENANT, pid, "thT")
    assert [t.user_message for t in turns] == ["first", "second"]
    assert [t.ai_response for t in turns] == ["r1", "r2"]


async def test_facets_lists_distinct_actors_and_sources():
    pid = _pid()
    await _add_trace(project=pid, thread_id="f1", actor="Alice", source="api")
    await _add_trace(project=pid, thread_id="f2", actor="System", source="playground")
    async with SessionLocal() as s:
        facets = await ConversationService.facets(s, TENANT, pid)
    assert "Alice" in facets["actors"] and "System" in facets["actors"]
    assert "api" in facets["sources"] and "playground" in facets["sources"]


async def test_purge_deletes_old_traces_and_spans():
    pid = _pid()
    old_id = await _add_trace(project=pid, thread_id="old", actor="X", source="api",
                              started=datetime.utcnow() - timedelta(days=40))
    await _add_trace(project=pid, thread_id="new", actor="X", source="api", started=datetime.utcnow())
    async with SessionLocal() as s:
        s.add(Span(tenant_id=TENANT, trace_id=old_id, name="tool", kind="tool"))
        await s.commit()

    async with SessionLocal() as s:
        removed = await ConversationService.purge_older_than(s, TENANT, pid, days=30)
    assert removed == 1
    async with SessionLocal() as s:
        convos = await ConversationService.list(s, TENANT, pid)
        remaining_spans = (await s.execute(select(Span).where(Span.trace_id == old_id))).scalars().all()
    assert [c.thread_id for c in convos] == ["new"]
    assert remaining_spans == []  # the old trace's spans were purged too


# --- end-to-end capture through a real run ------------------------------------

_WF = {
    "id": "wf_conv", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:Hi there!", "tools": []}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}


def _client() -> httpx.AsyncClient:
    app = create_app()
    app.state.checkpointer = InMemorySaver()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_run_captures_source_and_transcript_end_to_end():
    """A run through the project /run endpoint (source='api') must land as a conversation with
    the user message + AI response captured, so the Traces view can show it."""
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": f"u{uuid.uuid4().hex[:8]}@x.com", "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        pid = (await c.post("/v1/projects", json={"name": "Conv"}, headers=h)).json()["id"]
        wid = (await c.post(f"/v1/projects/{pid}/workflows", json={"name": "Chat", "executable": _WF}, headers=h)).json()["id"]
        await c.patch(f"/v1/projects/{pid}", json={"config": {"api_workflow_id": wid}}, headers=h)

        r = await c.post(f"/v1/projects/{pid}/run",
                         json={"input": {"messages": [{"role": "user", "content": "what is forge"}]}, "stream": False},
                         headers=h)
        assert r.status_code == 200, r.text

        convos = (await c.get(f"/v1/projects/{pid}/conversations", headers=h)).json()
        assert len(convos) == 1, convos
        conv = convos[0]
        assert conv["source"] == "api"
        assert conv["actor"] == "Unknown user"          # /run with no end_user identity
        assert conv["preview"] == "what is forge"

        detail = (await c.get(f"/v1/projects/{pid}/conversations/{conv['thread_id']}", headers=h)).json()
        turn = detail["turns"][0]
        assert turn["user_message"] == "what is forge"
        assert "Hi there!" in (turn["ai_response"] or "")
        # the AI-response click drills into the existing span waterfall by trace id
        assert turn["trace_id"]
        spans = (await c.get(f"/v1/projects/{pid}/traces/{turn['trace_id']}", headers=h)).json()
        assert "spans" in spans

        # The Traces "Run again" action must replay only through the original workflow.
        other_wid = (await c.post(
            f"/v1/projects/{pid}/workflows", json={"name": "Other", "executable": _WF}, headers=h,
        )).json()["id"]
        cross_workflow = await c.post(
            f"/v1/projects/{pid}/workflows/{other_wid}/runs/{turn['run_id']}/rerun", headers=h,
        )
        assert cross_workflow.status_code == 404

        replay = await c.post(
            f"/v1/projects/{pid}/workflows/{wid}/runs/{turn['run_id']}/rerun", headers=h,
        )
        assert replay.status_code == 201, replay.text
        replayed = replay.json()
        assert replayed["id"] != turn["run_id"]
        assert replayed["thread_id"] != conv["thread_id"]
