"""Per-run context injection (Feature: ephemeral per-run `run_context`).

A server-side caller passes an `X-Forge-Context` header on a run's EXECUTION request (stream /
resume); its values reach tools as {{ctx.<key>}} for on-behalf-of injection (e.g. a per-user
session cookie / CSRF token). They are NEVER persisted, NEVER placed in the LLM prompt, NEVER an
LLM-visible arg, and cannot be overridden by an LLM-supplied value.

Three lanes are kept distinct (the invariant these tests protect):
  - input parameters : `fields` with llm_visible=True -> the MODEL decides them (in args_schema)
  - injected context : {{ctx.*}} from run_context -> the SERVER injects them (not in schema)
  - auth providers   : project-scoped stored secrets (covered elsewhere)
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from forge.deps import FORGE_CONTEXT_HEADER, run_context
from forge.engine.context import CompileContext
from forge.tools import rest
from forge.tools.rest import build_args_schema, build_rest_tool, execute_rest


def _capturing_client(sink: dict) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        sink["request"] = request
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- consumption: values reach the outbound request ---------------------------------------


async def test_header_injection_from_context():
    sink: dict = {}
    cfg = {
        "name": "quotes_get",
        "request": {
            "method": "GET",
            "url_template": "https://portal.example.dev/quotes",
            "fields": [],
            "headers": [
                {"name": "Cookie", "value": "JSESSIONID={{ctx.jsessionid}}"},
                {"name": "X-CSRF-Token", "value": "{{ctx.csrf}}"},
            ],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(
            cfg, {}, tenant_id="t", project_id="p",
            context={"jsessionid": "S1", "csrf": "C1"}, client=client,
        )
    req = sink["request"]
    assert req.headers["cookie"] == "JSESSIONID=S1"
    assert req.headers["x-csrf-token"] == "C1"


async def test_body_field_injection_and_hidden_from_schema():
    """A non-llm-visible field with a {{ctx.*}} default injects into the body AND is not exposed
    to the model (not in the tool args schema) - the input-vs-injected separation."""
    sink: dict = {}
    cfg = {
        "name": "quote_add",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/quotes/entries",
            "fields": [
                {"path": "productCode", "type": "string", "in": "body", "required": True, "llm_visible": True},
                {"path": "CSRFToken", "in": "body", "llm_visible": False, "default": "{{ctx.csrf}}"},
            ],
            "headers": [],
        },
    }
    schema = build_args_schema(cfg)
    assert "productCode" in schema.model_fields  # the model DOES decide this
    assert "CSRFToken" not in schema.model_fields  # the server injects this; model never sees it

    async with _capturing_client(sink) as client:
        await execute_rest(
            cfg, {"productCode": "ABC-1"}, tenant_id="t", project_id="p",
            context={"csrf": "C1"}, client=client,
        )
    assert json.loads(sink["request"].content) == {"productCode": "ABC-1", "CSRFToken": "C1"}


async def test_query_field_injection():
    sink: dict = {}
    cfg = {
        "name": "quote_submit",
        "request": {
            "method": "GET",
            "url_template": "https://portal.example.dev/quote/submit",
            "fields": [{"path": "CSRFToken", "in": "query", "llm_visible": False, "default": "{{ctx.csrf}}"}],
            "headers": [],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", context={"csrf": "C1"}, client=client)
    assert sink["request"].url.params["CSRFToken"] == "C1"


async def test_url_template_ctx_injection():
    """{{ctx.*}} is honored directly in the URL/query string too."""
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "GET",
            "url_template": "https://portal.example.dev/quotes?token={{ctx.csrf}}",
            "fields": [],
            "headers": [],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", context={"csrf": "C1"}, client=client)
    assert sink["request"].url.params["token"] == "C1"


async def test_cookie_field_injection():
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "GET",
            "url_template": "https://portal.example.dev/x",
            "fields": [{"path": "JSESSIONID", "in": "cookie", "llm_visible": False, "default": "{{ctx.jsessionid}}"}],
            "headers": [],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", context={"jsessionid": "S1"}, client=client)
    assert "JSESSIONID=S1" in sink["request"].headers.get("cookie", "")


async def test_body_template_json_with_input_and_ctx():
    """A free-form JSON body template mixes {{input.*}} (model args) and {{ctx.*}} (injected)."""
    sink: dict = {}
    cfg = {
        "name": "quote_add",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/quotes",
            "fields": [{"path": "amount", "type": "integer", "in": "body", "llm_visible": True}],
            "headers": [],
            "body_template": '{"amount": {{ input.amount }}, "CSRFToken": "{{ ctx.csrf }}"}',
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {"amount": 5}, tenant_id="t", project_id="p", context={"csrf": "C1"}, client=client)
    assert json.loads(sink["request"].content) == {"amount": 5, "CSRFToken": "C1"}


async def test_body_template_non_json_sent_raw():
    """A body template that isn't JSON (e.g. form-encoded) is sent as raw content, not quoted."""
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/x",
            "fields": [],
            "headers": [],
            "body_template": "CSRFToken={{ctx.csrf}}&scope=all",
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", context={"csrf": "C1"}, client=client)
    assert sink["request"].content == b"CSRFToken=C1&scope=all"


# --- security invariants -------------------------------------------------------------------


async def test_ctx_header_is_authoritative_over_llm_field():
    """An LLM-supplied header field must NOT override a server-injected ctx-templated header."""
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "GET",
            "url_template": "https://portal.example.dev/x",
            "fields": [{"path": "X-CSRF-Token", "type": "string", "in": "header", "llm_visible": True}],
            "headers": [{"name": "X-CSRF-Token", "value": "{{ctx.csrf}}"}],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(
            cfg, {"X-CSRF-Token": "attacker-supplied"}, tenant_id="t", project_id="p",
            context={"csrf": "C1"}, client=client,
        )
    assert sink["request"].headers["x-csrf-token"] == "C1"


async def test_missing_context_value_is_dropped_not_literal():
    """A {{ctx.*}} default with no matching context value is omitted - never sent as the raw
    template string (regression for the _collect raw-default fallback)."""
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/x",
            "fields": [{"path": "CSRFToken", "in": "body", "llm_visible": False, "default": "{{ctx.csrf}}"}],
            "headers": [],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", context={}, client=client)
    # No body was sent (the only field resolved to None and was dropped, not sent literally).
    assert sink["request"].content in (b"", b"null")


async def test_run_context_and_end_user_are_distinct_lanes():
    """Via build_rest_tool: ctx.run_context supplies {{ctx.csrf}} while ctx.end_user supplies
    {{ctx.end_user.id}} - both resolve, and the secret is not part of end_user (identity)."""
    sink: dict = {}
    cfg = {
        "name": "on_behalf",
        "request": {
            "method": "GET",
            "url_template": "https://portal.example.dev/me",
            "fields": [],
            "headers": [
                {"name": "X-CSRF-Token", "value": "{{ctx.csrf}}"},
                {"name": "X-User", "value": "{{ctx.end_user.id}}"},
            ],
        },
    }
    ctx = CompileContext(tenant_id="t", project_id="p")
    ctx.run_context = {"csrf": "C1"}
    ctx.end_user = {"id": "u1"}

    class _RT:
        context: dict = {}
        stream_writer = None

    tool = build_rest_tool(cfg, ctx)
    async with _capturing_client(sink) as client:
        # The tool coroutine calls execute_rest with the shared client; point it at ours.
        import forge.tools.rest as rest_mod

        orig = rest_mod.shared_async_client
        rest_mod.shared_async_client = lambda: client  # type: ignore[assignment]
        try:
            await tool.coroutine(runtime=_RT())
        finally:
            rest_mod.shared_async_client = orig  # type: ignore[assignment]

    req = sink["request"]
    assert req.headers["x-csrf-token"] == "C1"
    assert req.headers["x-user"] == "u1"
    # The secret is NOT part of identity (so it never reaches the end_user prompt block).
    assert "csrf" not in ctx.end_user


# --- transport: header parsing -------------------------------------------------------------


class _Req:
    def __init__(self, headers: dict):
        self.headers = headers


def test_run_context_parses_json_object():
    r = _Req({FORGE_CONTEXT_HEADER: '{"csrf": "C1", "jsessionid": "S1"}'})
    assert run_context(r) == {"csrf": "C1", "jsessionid": "S1"}


def test_run_context_absent_is_none():
    assert run_context(_Req({})) is None


def test_run_context_strips_end_user():
    r = _Req({FORGE_CONTEXT_HEADER: '{"end_user": {"id": "x"}, "csrf": "C1"}'})
    assert run_context(r) == {"csrf": "C1"}  # identity is not settable via this channel


def test_run_context_rejects_invalid_json():
    with pytest.raises(HTTPException) as e:
        run_context(_Req({FORGE_CONTEXT_HEADER: "not-json"}))
    assert e.value.status_code == 400


def test_run_context_rejects_non_object():
    with pytest.raises(HTTPException) as e:
        run_context(_Req({FORGE_CONTEXT_HEADER: '"just-a-string"'}))
    assert e.value.status_code == 400


def test_run_context_rejects_oversized():
    big = json.dumps({"x": "a" * 9000})
    with pytest.raises(HTTPException) as e:
        run_context(_Req({FORGE_CONTEXT_HEADER: big}))
    assert e.value.status_code == 413
