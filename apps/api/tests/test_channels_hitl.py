"""Channels + HITL + run-cancel + semantic-cache wiring (audit a-i).

Covers: the semantic_cache middleware (short-circuit on hit), handoff TOCTOU claim +
delivery-status gating + chained-interrupt re-open + decision coercion, HITL timeout expiry,
run cancel, email HTML fallback + threading, Teams card actions, and pluggable webhook
signatures. Uses the shared checkpointer pattern (InMemorySaver) so runs can be resumed by id.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from forge.channels import email as email_ch
from forge.channels import teams as teams_ch
from forge.db.base import SessionLocal
from forge.models import HandoffRequest, Run, Workflow
from forge.routers import hooks
from forge.services.channels import ChannelService
from forge.services.dispatch import dispatch_message
from forge.services.handoff import HandoffService, coerce_to_allowed_decision
from forge.services.runs import RunService

# --- workflow fixtures --------------------------------------------------------------------

_HITL_ONE = {
    "id": "wf_one", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "hi1",
    "on_error": {"message": "We'll follow up soon."},
    "nodes": [
        {"id": "hi1", "type": "human_input", "config": {"prompt": "Approve?", "allowed_decisions": ["approve", "reject"]}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "hi1", "target": "end"}],
}

_HITL_CHAIN = {
    "id": "wf_chain", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "hi1",
    "nodes": [
        {"id": "hi1", "type": "human_input", "config": {"prompt": "Approve step 1?", "allowed_decisions": ["approve", "reject"]}},
        {"id": "hi2", "type": "human_input", "config": {"prompt": "Confirm step 2?", "ack_message": "One more step - confirming.", "allowed_decisions": ["approve", "reject"]}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "hi1", "target": "hi2"}, {"source": "hi2", "target": "end"}],
}


async def _mk_wf(executable, tenant, project) -> Workflow:
    async with SessionLocal() as s:
        wf = Workflow(tenant_id=tenant, project_id=project, name="W", executable=executable, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        return wf


# --- (a) semantic-cache middleware --------------------------------------------------------


def test_semantic_cache_middleware_registered_and_builds():
    from forge.engine.context import CompileContext
    from forge.engine.middleware_compiler import (
        MW_BUILDERS,
        _SemanticCacheMiddleware,
        build_middleware,
    )

    assert "semantic_cache" in MW_BUILDERS
    mw = build_middleware([{"type": "semantic_cache", "config": {"threshold": 0.9, "ttl": 60}}],
                          CompileContext(tenant_id="t", project_id="p"))
    assert len(mw) == 1 and isinstance(mw[0], _SemanticCacheMiddleware)


async def test_semantic_cache_middleware_short_circuits_on_hit(monkeypatch):
    from langchain.agents.middleware.types import ModelResponse

    from forge.engine.middleware_compiler import _SemanticCacheMiddleware

    calls = {"handler": 0, "store": 0}
    stored: dict[str, str] = {}

    async def fake_lookup(session, t, p, q, *, scope, threshold, ttl):
        return stored.get(q.strip().lower())

    async def fake_store(session, t, p, q, a, *, scope):
        calls["store"] += 1
        stored[q.strip().lower()] = a

    monkeypatch.setattr("forge.services.semantic_cache.SemanticCacheService.lookup", fake_lookup)
    monkeypatch.setattr("forge.services.semantic_cache.SemanticCacheService.store", fake_store)

    async def handler(_req):
        calls["handler"] += 1
        return ModelResponse(result=[AIMessage(content="We are open 9-5.")])

    mw = _SemanticCacheMiddleware("t", "p", threshold=0.9, ttl=3600, scope="default", min_chars=3)
    q = "what are your business hours?"

    r1 = await mw.awrap_model_call(SimpleNamespace(messages=[HumanMessage(content=q)]), handler)
    assert calls == {"handler": 1, "store": 1}
    assert r1.result[0].content == "We are open 9-5."

    # Same question again -> cache hit -> handler is NOT called, a cached AIMessage is returned.
    r2 = await mw.awrap_model_call(SimpleNamespace(messages=[HumanMessage(content=q)]), handler)
    assert calls["handler"] == 1
    assert isinstance(r2, AIMessage) and "9-5" in r2.content


async def test_semantic_cache_middleware_skips_mid_tool_loop(monkeypatch):
    from langchain.agents.middleware.types import ModelResponse

    from forge.engine.middleware_compiler import _SemanticCacheMiddleware

    looked_up = []

    async def fake_lookup(session, t, p, q, *, scope, threshold, ttl):
        looked_up.append(q)
        return None

    monkeypatch.setattr("forge.services.semantic_cache.SemanticCacheService.lookup", fake_lookup)
    monkeypatch.setattr("forge.services.semantic_cache.SemanticCacheService.store", lambda *a, **k: None)

    async def handler(_req):
        return ModelResponse(result=[AIMessage(content="x")])

    mw = _SemanticCacheMiddleware("t", "p", threshold=0.9, ttl=3600, scope="default", min_chars=3)
    # Last message is a tool result (not a fresh human question) -> no lookup / store.
    from langchain_core.messages import ToolMessage
    req = SimpleNamespace(messages=[HumanMessage(content="hi"), AIMessage(content=""), ToolMessage(content="42", tool_call_id="c1")])
    await mw.awrap_model_call(req, handler)
    assert looked_up == []


async def test_semantic_cache_purge(monkeypatch):
    from forge.services.semantic_cache import SemanticCacheService

    t, p = "t_purge", "p_purge"
    async with SessionLocal() as s:
        await SemanticCacheService.store(s, t, p, "will this expire?", "yes")
    async with SessionLocal() as s:
        # ttl<=0 purges everything for the scope; returns the count purged.
        purged = await SemanticCacheService.purge(s, t, p, ttl=0)
    assert purged >= 1


# --- (c) decision coercion ----------------------------------------------------------------


def test_coerce_to_allowed_decision():
    allowed = ["approve", "reject"]
    assert coerce_to_allowed_decision("approve", allowed) == "approve"
    assert coerce_to_allowed_decision("Yes, go ahead", allowed) == "approve"
    assert coerce_to_allowed_decision("please approve this refund", allowed) == "approve"
    assert coerce_to_allowed_decision("no, deny it", allowed) == "reject"
    # Ambiguous free text fails safe to a negative decision when one is offered.
    assert coerce_to_allowed_decision("hmm not sure yet", allowed) == "reject"
    # No allowed list -> passthrough (unchanged behavior).
    assert coerce_to_allowed_decision("whatever", []) == "whatever"


# --- (b) handoff: TOCTOU claim, coercion end-to-end, chained re-open ----------------------


async def test_handoff_reply_claims_row_toctou():
    wf = await _mk_wf(_HITL_ONE, "t_toctou", "p_toctou")
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_message(rs, tenant_id="t_toctou", project_id="p_toctou", workflow_id=wf.id, text="please")
    assert result["interrupted"] is True
    async with SessionLocal() as s:
        h = await HandoffService.create(
            s, channel=None, tenant_id="t_toctou", project_id="p_toctou", workflow_id=wf.id,
            run_id=result["run_id"], thread_id=result["thread_id"], customer="u",
            customer_message="please", reason="approve?", reply_context={},
        )
        out1 = await HandoffService.reply(s, rs, handoff=h, agent_id="a1", message="approve")
        assert out1["ok"] is True
        # Second reply on the now-answered handoff is rejected by the atomic claim (no re-resume).
        out2 = await HandoffService.reply(s, rs, handoff=h, agent_id="a2", message="approve")
        assert out2["ok"] is False and out2["status"] == "answered"


async def test_handoff_reply_coerces_free_text_decision():
    wf = await _mk_wf(_HITL_ONE, "t_coerce", "p_coerce")
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_message(rs, tenant_id="t_coerce", project_id="p_coerce", workflow_id=wf.id, text="please")
    async with SessionLocal() as s:
        h = await HandoffService.create(
            s, channel=None, tenant_id="t_coerce", project_id="p_coerce", workflow_id=wf.id,
            run_id=result["run_id"], thread_id=result["thread_id"], customer="u",
            customer_message="please", reason="approve?",
            reply_context={"_forge_hitl": {"allowed_decisions": ["approve", "reject"]}},
        )
        out = await HandoffService.reply(s, rs, handoff=h, agent_id="a1", message="yes, go ahead")
    assert out["ok"] is True
    # The free-text "yes, go ahead" was coerced to "approve" before resuming.
    msgs = out["resume"]["messages"]
    assert any("[human decision] approve" in str(m.get("content", "")) for m in msgs)


async def test_handoff_reply_reopens_on_chained_interrupt():
    wf = await _mk_wf(_HITL_CHAIN, "t_chain", "p_chain")
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_message(rs, tenant_id="t_chain", project_id="p_chain", workflow_id=wf.id, text="start")
    assert result["interrupted"] is True
    async with SessionLocal() as s:
        ch = await ChannelService.create(s, "t_chain", "p_chain", type_="teams", name="T", workflow_id=wf.id)
        h = await HandoffService.create(
            s, channel=ch, tenant_id="t_chain", project_id="p_chain", workflow_id=wf.id,
            run_id=result["run_id"], thread_id=result["thread_id"], customer="u",
            customer_message="start", reason="step1", reply_context={},
        )
        out = await HandoffService.reply(s, rs, handoff=h, agent_id="a1", message="approve")
    assert out["ok"] is True and out["reinterrupted"] is True
    assert out["new_handoff_id"]
    # A fresh open handoff exists for the same run so the next step is actionable.
    async with SessionLocal() as s:
        fresh = await s.get(HandoffRequest, out["new_handoff_id"])
        assert fresh is not None and fresh.status == "open" and fresh.run_id == result["run_id"]


# --- (e) delivery status gates 'answered' -------------------------------------------------


async def test_handoff_failed_send_not_marked_answered(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_ch, "send_reply", _boom)
    wf = await _mk_wf(_HITL_ONE, "t_fail", "p_fail")
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_message(rs, tenant_id="t_fail", project_id="p_fail", workflow_id=wf.id, text="please")
    async with SessionLocal() as s:
        ch = await ChannelService.create(s, "t_fail", "p_fail", type_="email", name="E", workflow_id=wf.id)
        h = await HandoffService.create(
            s, channel=ch, tenant_id="t_fail", project_id="p_fail", workflow_id=wf.id,
            run_id=result["run_id"], thread_id=result["thread_id"], customer="c@x.com",
            customer_message="please", reason="approve?", reply_context={"from_addr": "c@x.com"},
        )
        out = await HandoffService.reply(s, rs, handoff=h, agent_id="a1", message="Here you go")
        assert out["ok"] is False and out["status"] == "delivery_failed"
        refreshed = await s.get(HandoffRequest, h.id)
        assert refreshed.status == "delivery_failed"  # NOT 'answered' on a failed send


# --- (c) HITL timeout expiry --------------------------------------------------------------


async def test_hitl_timeout_expires_interrupted_run(monkeypatch):
    delivered: list[str] = []

    async def _rec_deliver(channel, reply_ctx, text):
        delivered.append(text)
        return True

    monkeypatch.setattr("forge.services.handoff._deliver", _rec_deliver)
    wf = await _mk_wf(_HITL_ONE, "t_exp", "p_exp")
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_message(rs, tenant_id="t_exp", project_id="p_exp", workflow_id=wf.id, text="please")
    run_id = result["run_id"]
    async with SessionLocal() as s:
        ch = await ChannelService.create(s, "t_exp", "p_exp", type_="teams", name="T", workflow_id=wf.id)
        h = await HandoffService.create(
            s, channel=ch, tenant_id="t_exp", project_id="p_exp", workflow_id=wf.id,
            run_id=run_id, thread_id=result["thread_id"], customer="u", customer_message="please",
            reason="approve?", reply_context={"conversation_id": "c1"},
        )
        hid = h.id
        # Backdate the pause so it's past the (tiny) timeout used below.
        run = await s.get(Run, run_id)
        run.ended_at = datetime.utcnow() - timedelta(hours=1)
        await s.commit()

    reaped = await RunService.reap_stale_runs(hitl_timeout_s=1)
    assert reaped >= 1
    async with SessionLocal() as s:
        run = await s.get(Run, run_id)
        assert run.status == "error" and "HITL" in (run.error or "")
        h = await s.get(HandoffRequest, hid)
        assert h.status == "closed"
    # The workflow's on_error fallback was pushed over the channel.
    assert delivered and delivered[0] == "We'll follow up soon."


# --- (h) run cancel -----------------------------------------------------------------------


async def test_cancel_run_marks_canceled_and_is_idempotent():
    wf = await _mk_wf(_HITL_ONE, "t_cancel", "p_cancel")
    rs = RunService(checkpointer=InMemorySaver())
    async with SessionLocal() as s:
        run = await rs.create_run(s, tenant_id="t_cancel", project_id="p_cancel", workflow_id=wf.id, input={})
        run_id = run.id
    out = await rs.cancel_run(run_id=run_id, tenant_id="t_cancel", project_id="p_cancel")
    assert out["ok"] is True and out["status"] == "canceled"
    async with SessionLocal() as s:
        assert (await s.get(Run, run_id)).status == "canceled"
    # Cancelling an already-terminal run is a no-op.
    out2 = await rs.cancel_run(run_id=run_id, tenant_id="t_cancel", project_id="p_cancel")
    assert out2["ok"] is False and out2["status"] == "canceled"


# --- (f) email HTML fallback + (g) threading ----------------------------------------------


def test_email_html_only_fallback():
    p = email_ch.parse_inbound({"from": "a@b.com", "subject": "Hi",
                                "html": "<p>Hello <b>world</b></p><div>Line two</div>"})
    assert "Hello world" in p["text"] and "Line two" in p["text"]


def test_email_raw_html_only_fallback():
    raw = (b"From: a@b.com\r\nSubject: Hi\r\nContent-Type: text/html\r\n\r\n"
           b"<html><body><p>Body text here</p></body></html>\r\n")
    p = email_ch.parse_inbound(raw)
    assert "Body text here" in p["text"]


def test_email_reply_preserves_references_and_sets_message_id():
    msg = email_ch.build_reply(to_addr="a@b.com", subject="Order", body="ok", from_addr="bot@x.com",
                               in_reply_to="<m2>", references="<m0> <m1>")
    assert msg["In-Reply-To"] == "<m2>"
    assert msg["References"].split() == ["<m0>", "<m1>", "<m2>"]
    assert msg["Message-ID"]  # explicit id set on the outbound reply


# --- (d) Teams card actions + card build + JWT gate ---------------------------------------


def test_teams_card_action_text():
    parsed = teams_ch.parse_activity({
        "type": "message", "value": {"action": "refund", "text": "Approve refund"},
        "from": {"id": "u"}, "conversation": {"id": "c"}, "serviceUrl": "https://smba.x/",
    })
    assert parsed["text"] == ""  # a card submit has no typed text
    assert teams_ch.inbound_text(parsed) == "Approve refund"


def test_teams_build_card_activity():
    incoming = teams_ch.parse_activity({
        "type": "message", "id": "a1", "from": {"id": "u"}, "recipient": {"id": "b"},
        "conversation": {"id": "c1"}, "serviceUrl": "https://smba.x/",
    })
    card = {"type": "AdaptiveCard", "body": []}
    act = teams_ch.build_card_activity(incoming, card, text="fallback")
    att = act["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["content"] == card and act["conversation"]["id"] == "c1"


async def test_teams_verify_jwt_rejects_missing_and_malformed():
    # No network needed: missing token / malformed token fail before any JWKS fetch.
    assert await teams_ch.verify_bot_jwt(None, app_id="app") is False
    assert await teams_ch.verify_bot_jwt("Bearer not-a-jwt", app_id="app") is False


# --- (i) pluggable webhook signatures -----------------------------------------------------


def test_webhook_stripe_signature():
    secret = "whsec_test"
    body = b'{"id":"evt_1"}'
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    req = SimpleNamespace(headers={"Stripe-Signature": f"t={ts},v1={mac}"})
    assert hooks._verify_stripe(secret, req, body, 300) is True
    # Tampered body fails.
    assert hooks._verify_stripe(secret, req, b'{"id":"evt_2"}', 300) is False
    # Stale timestamp fails the replay window.
    old = SimpleNamespace(headers={"Stripe-Signature": f"t=1,v1={mac}"})
    assert hooks._verify_stripe(secret, old, body, 300) is False


def test_webhook_slack_signature():
    secret = "slack_secret"
    body = b"token=abc&team_id=T1"
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    req = SimpleNamespace(headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": f"v0={mac}"})
    assert hooks._verify_slack(secret, req, body, 300) is True
    bad = SimpleNamespace(headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": "v0=deadbeef"})
    assert hooks._verify_slack(secret, bad, body, 300) is False


def test_webhook_default_hmac_signature():
    secret = "s3cr3t"
    body = b"payload"
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = SimpleNamespace(headers={"X-Hub-Signature-256": f"sha256={mac}"})
    assert hooks._verify_hmac_sha256(secret, req, body) is True
    req2 = SimpleNamespace(headers={"x-forge-signature": mac})
    assert hooks._verify_hmac_sha256(secret, req2, body) is True
