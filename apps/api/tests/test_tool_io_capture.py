"""Tool-I/O capture for traces.

A REST tool records its FRAMED request (resolved URL, query, headers/cookies templated
from {{ctx.*}}) plus the response into a context var; the ForgeTracer reads it back onto
the tool span. This is what makes a run-time "works in test, 401s in a run" visible: the
capture runs on failure too, so a dropped ctx cookie / a 401 shows up in the trace.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from forge.config import settings
from forge.tools import rest as rest_mod
from forge.tools.rest import execute_rest
from forge.tracing import tool_io
from forge.tracing.tracer import ForgeTracer


def _cfg(**extra) -> dict:
    return {
        "name": f"quote_{uuid.uuid4().hex[:6]}",
        "kind": "rest_api",
        "request": {
            "method": "GET",
            "url_template": "https://api.acme.dev/quote/{id}",
            "fields": [
                {"path": "id", "in": "path", "llm_visible": True},
                {"path": "q", "in": "query", "llm_visible": True},
                # server-injected session cookie, templated from run context (not an LLM arg)
                {"path": "sid", "in": "cookie", "default": "{{ctx.jsessionid}}", "llm_visible": False},
            ],
            "headers": [{"name": "X-CSRF-Token", "value": "{{ctx.csrf}}"}],
        },
        **extra,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_capture_frames_request_and_response():
    tool_io.clear_tool_io()
    client = _client(lambda r: httpx.Response(200, json={"ok": True}))
    await execute_rest(
        _cfg(), {"id": "123", "q": "widget"},
        tenant_id="t", project_id="p", context={"jsessionid": "SESS", "csrf": "CSRF-XYZ"}, client=client,
    )
    await client.aclose()

    rec = tool_io.take_tool_io()
    assert rec is not None
    inp, out = rec["input"], rec["output"]
    # the agent's own args, and the fully framed request
    assert inp["args"] == {"id": "123", "q": "widget"}
    assert inp["method"] == "GET"
    assert inp["url"] == "https://api.acme.dev/quote/123"   # path param substituted
    assert inp["query"] == {"q": "widget"}
    assert inp["headers"]["X-CSRF-Token"] == "CSRF-XYZ"     # {{ctx.csrf}} resolved (full, redact off)
    assert inp["cookies"]["sid"] == "SESS"                  # {{ctx.jsessionid}} resolved
    assert out["status"] == 200 and out["response"] == {"ok": True} and out["error"] is None


async def test_capture_records_a_failed_call():
    """A 401 raises out of execute_rest, but the framed request + status must still be captured
    so the failure is visible in the trace (not silently swallowed)."""
    tool_io.clear_tool_io()
    client = _client(lambda r: httpx.Response(401, json={"detail": "no session"}))
    with pytest.raises(httpx.HTTPStatusError):
        await execute_rest(
            _cfg(), {"id": "9"},
            tenant_id="t", project_id="p", context={}, client=client,  # no ctx -> cookie/csrf dropped
        )
    await client.aclose()

    rec = tool_io.take_tool_io()
    assert rec is not None
    assert rec["output"]["status"] == 401 and rec["output"]["error"]
    # the dropped session is visible by its ABSENCE: no sid cookie / empty csrf were sent
    assert "sid" not in rec["input"]["cookies"]


async def test_redaction_masks_secrets_when_enabled(monkeypatch):
    tool_io.clear_tool_io()
    monkeypatch.setattr(settings, "trace_tool_io_redact", True)
    client = _client(lambda r: httpx.Response(200, json={"ok": True}))
    await execute_rest(
        _cfg(), {"id": "1"},
        tenant_id="t", project_id="p", context={"jsessionid": "SESS", "csrf": "CSRF-XYZ"}, client=client,
    )
    await client.aclose()

    rec = tool_io.take_tool_io()
    assert rec["input"]["headers"]["X-CSRF-Token"].startswith("•••")   # masked
    assert rec["input"]["cookies"]["sid"].startswith("•••")
    assert "CSRF-XYZ" not in str(rec["input"])                          # secret not persisted


async def test_disabled_capture_is_a_noop(monkeypatch):
    tool_io.clear_tool_io()
    monkeypatch.setattr(settings, "trace_tool_io", False)
    client = _client(lambda r: httpx.Response(200, json={"ok": True}))
    await execute_rest(_cfg(), {"id": "1"}, tenant_id="t", project_id="p", context={}, client=client)
    await client.aclose()
    assert tool_io.take_tool_io() is None


def test_tracer_attaches_matching_record_to_tool_span():
    """on_tool_end merges a record whose name matches the span's tool."""
    tool_io.clear_tool_io()
    tr = ForgeTracer()
    rid = uuid.uuid4()
    tr.on_tool_start({"name": "quote"}, '{"id": "1"}', run_id=rid)
    tool_io.set_tool_io("quote", request={"method": "GET", "url": "u"}, response={"status": 200})
    tr.on_tool_end({"ok": True}, run_id=rid)

    sp = tr.spans[str(rid)]
    assert sp.input == {"method": "GET", "url": "u"} and sp.output == {"status": 200}


def test_span_dto_exposes_io():
    """The traces API must serialize the captured input/output to the web client."""
    from forge.models import Span
    from forge.schemas.dto import SpanOut

    sp = Span(
        id="s1", tenant_id="t", trace_id="tr", name="tool · quote", kind="tool", latency_ms=5,
        input={"method": "GET", "url": "https://api.acme.dev/quote/1"}, output={"status": 200},
        input_tokens=0, output_tokens=0, cost_usd=0.0,
    )
    dto = SpanOut.model_validate(sp)
    assert dto.input == {"method": "GET", "url": "https://api.acme.dev/quote/1"}
    assert dto.output == {"status": 200}


def test_tracer_ignores_stale_record_from_another_tool():
    """A record left by an EARLIER tool must not attach to a different tool's span; that span
    falls back to its own raw return value instead."""
    tool_io.clear_tool_io()
    tr = ForgeTracer()
    rid = uuid.uuid4()
    tr.on_tool_start({"name": "lookup"}, "{}", run_id=rid)
    tool_io.set_tool_io("some_other_tool", request={"method": "GET"}, response={"status": 200})
    tr.on_tool_end("plain string result", run_id=rid)

    sp = tr.spans[str(rid)]
    assert sp.output == "plain string result"          # fell back to the raw return
    assert sp.input == "{}"                             # kept the provisional LLM args
