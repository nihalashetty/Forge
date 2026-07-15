"""Hardening tests for the tool executors (robustness-audit fixes).

Covers: retry semantics (idempotency + transient-only default), the shared entitlement gate for
every non-REST kind, reliability parity (rate_limit/cache) for graphql/sql/code via the shared
wrapper, JMESPath projection error markers, SQL read-only/limit/cell-cap hardening, GraphQL
in-band error handling + operationName, mcp being creatable, REST multipart + download-size guard,
and production-default trace redaction.
"""

from __future__ import annotations

import sqlite3
import uuid

import httpx
import pytest

from forge.services.runtime import make_runtime_ctx
from forge.tools import rest as rest_mod
from forge.tools.graphql import GraphQLToolError, execute_graphql
from forge.tools.materialize import materialize_tool
from forge.tools.projection import project_response
from forge.tools.rest import _resolve_retry, _retry_types, _should_retry, execute_rest
from forge.tools.sql import SqlToolError, execute_sql


@pytest.fixture(autouse=True)
def _enable_code_tools(monkeypatch):
    from forge.config import settings

    monkeypatch.setattr(settings, "enable_code_tools", True)


def _rest_cfg(**extra) -> dict:
    return {
        "name": f"t_{uuid.uuid4().hex[:8]}",
        "kind": "rest_api",
        "request": {"method": "GET", "url_template": "https://api.acme.dev/v2/ping", "fields": []},
        **extra,
    }


# --- Finding 1: retry semantics (transient-only default, idempotency gating, schema default) ----


def test_should_retry_gates_on_idempotency_and_status():
    transient = _retry_types([])
    # 4xx is never retried by default (regression: HTTPError superclass used to retry it).
    resp4 = httpx.Response(404, request=httpx.Request("GET", "https://x"))
    err4 = httpx.HTTPStatusError("nf", request=resp4.request, response=resp4)
    assert _should_retry(err4, transient, True, "GET", {}) is False
    # 5xx IS retried on an idempotent method by the default classification...
    resp5 = httpx.Response(503, request=httpx.Request("GET", "https://x"))
    err5 = httpx.HTTPStatusError("boom", request=resp5.request, response=resp5)
    assert _should_retry(err5, transient, True, "GET", {}) is True
    # ...but NOT on a non-idempotent POST unless explicitly opted in.
    assert _should_retry(err5, transient, True, "POST", {}) is False
    assert _should_retry(err5, transient, True, "POST", {"retry_non_idempotent": True}) is True


def test_resolve_retry_defaults_align_with_schema():
    # No retry block => opt-out (no retries), preserving historic behavior.
    assert _resolve_retry({})[0] == 0
    # A retry block present but max_retries omitted => schema default of 2.
    assert _resolve_retry({"retry": {}})[0] == 2
    assert _resolve_retry({"retry": {"max_retries": 5}})[0] == 5


async def test_default_retry_does_not_retry_4xx():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(404, json={"e": "nope"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = _rest_cfg(retry={"max_retries": 3, "initial_delay": 0.001, "jitter": False})
    with pytest.raises(httpx.HTTPStatusError):
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert calls["n"] == 1  # a permanent 4xx is not retried


async def test_default_retry_retries_transient_5xx_on_get():
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        return httpx.Response(500 if state["n"] == 1 else 200, json={"ok": state["n"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = _rest_cfg(retry={"max_retries": 2, "initial_delay": 0.001, "jitter": False})  # no retry_on
    res = await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert state["n"] == 2 and res["status"] == 200


async def test_post_5xx_not_retried_by_default():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = _rest_cfg(retry={"max_retries": 3, "initial_delay": 0.001, "jitter": False})
    cfg["request"]["method"] = "POST"
    with pytest.raises(httpx.HTTPStatusError):
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert calls["n"] == 1  # non-idempotent POST is not auto-retried


# --- Finding 4: broken JMESPath projection -> structured error marker (not silent full payload) --


def test_broken_jmespath_returns_error_marker():
    data = {"secret": "x" * 100, "items": [1, 2, 3]}
    out = project_response(data, {"projection_jmespath": "items[?"})  # malformed expression
    assert isinstance(out, dict) and out.get("error") == "projection_error"
    assert "expression" in out and out != data  # did NOT masquerade as the full payload


def test_valid_jmespath_missing_key_is_not_error():
    out = project_response({"a": 1}, {"projection_jmespath": "nope.missing"})
    assert out is None  # a valid expression selecting nothing is not an error


# --- Finding 5: SQL read-only / limit / cell-cap hardening ---------------------------------------


def _sqlite_url(tmp_path, rows=5) -> str:
    db = tmp_path / f"h_{uuid.uuid4().hex[:6]}.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE t(id INTEGER, name TEXT);")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"n{i}") for i in range(1, rows + 1)])
    con.commit()
    con.close()
    return f"sqlite+aiosqlite:///{db.as_posix()}"


async def test_sql_forbids_into_outfile(tmp_path):
    cfg = {"name": "q", "kind": "sql", "connection_url": _sqlite_url(tmp_path),
           "query": "SELECT * FROM t INTO OUTFILE '/tmp/x'"}
    with pytest.raises(SqlToolError):
        await execute_sql(cfg, {}, tenant_id="t", project_id="p")


async def test_sql_streaming_truncation_is_accurate(tmp_path):
    url = _sqlite_url(tmp_path, rows=5)
    over = await execute_sql({"name": "q", "kind": "sql", "connection_url": url,
                              "query": "SELECT id FROM t ORDER BY id", "max_rows": 3}, {},
                             tenant_id="t", project_id="p")
    assert over["row_count"] == 3 and over["truncated"] is True
    exact = await execute_sql({"name": "q", "kind": "sql", "connection_url": url,
                               "query": "SELECT id FROM t ORDER BY id", "max_rows": 5}, {},
                              tenant_id="t", project_id="p")
    assert exact["row_count"] == 5 and exact["truncated"] is False  # exactly max_rows is not truncated


async def test_sql_caps_large_cell(tmp_path):
    cfg = {"name": "q", "kind": "sql", "connection_url": _sqlite_url(tmp_path, rows=1),
           "query": "SELECT printf('%.*c', 30000, 'x') AS big"}
    res = await execute_sql(cfg, {}, tenant_id="t", project_id="p")
    big = res["rows"][0]["big"]
    assert len(big) < 30000 and "truncated" in big


# --- Finding 7: GraphQL in-band errors + operationName -------------------------------------------


async def test_graphql_errors_with_null_data_raise():
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": None, "errors": [{"message": "boom"}]})
    ))
    cfg = {"name": "g", "kind": "graphql", "endpoint": "https://api.acme.dev/graphql", "query": "{ me { id } }"}
    with pytest.raises(GraphQLToolError, match="boom"):
        await execute_graphql(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()


async def test_graphql_partial_data_passes_through():
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"me": {"id": "1"}}, "errors": [{"message": "field x failed"}]})
    ))
    cfg = {"name": "g", "kind": "graphql", "endpoint": "https://api.acme.dev/graphql", "query": "{ me { id } }"}
    res = await execute_graphql(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert res["raw"]["data"] == {"me": {"id": "1"}}  # partial success is valid GraphQL


async def test_graphql_sends_operation_name():
    seen = {}

    def handler(req):
        import json as _j

        seen.update(_j.loads(req.content))
        return httpx.Response(200, json={"data": {"ok": True}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = {"name": "g", "kind": "graphql", "endpoint": "https://api.acme.dev/graphql",
           "query": "query A { a } query B { b }", "operation_name": "B"}
    await execute_graphql(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert seen.get("operationName") == "B"


# --- Finding 2: entitlement gate for every non-REST kind (deny independently of the LLM) ---------


def _ctx_without(entitlement="billing:read"):
    ctx = make_runtime_ctx("t", "p")
    ctx.end_user = {"id": "u1", "entitlements": []}  # user lacks the required entitlement
    return ctx


async def test_graphql_entitlement_denied():
    ctx = _ctx_without()
    cfg = {"name": "g", "kind": "graphql", "endpoint": "https://api.acme.dev/graphql",
           "query": "{ me { id } }", "required_entitlements": ["billing:read"]}
    tool = materialize_tool(cfg, ctx)
    out = await tool.ainvoke({})
    assert "Not permitted" in out  # denied before any network call


async def test_sql_entitlement_denied(tmp_path):
    ctx = _ctx_without()
    cfg = {"name": "q", "kind": "sql", "connection_url": _sqlite_url(tmp_path),
           "query": "SELECT id FROM t", "required_entitlements": ["billing:read"]}
    tool = materialize_tool(cfg, ctx)
    out = await tool.ainvoke({})
    assert "Not permitted" in out


async def test_code_entitlement_denied():
    ctx = _ctx_without()
    cfg = {"name": "c", "kind": "code", "language": "python",
           "source": "def main():\n    return 1\n", "required_entitlements": ["billing:read"]}
    tool = materialize_tool(cfg, ctx)
    out = await tool.ainvoke({})
    assert "Not permitted" in out


async def test_component_entitlement_denied():
    from forge.tools.components import build_component_tool

    ctx = _ctx_without()
    cfg = {"id": "c1", "name": "chart", "props_schema": {}, "required_entitlements": ["billing:read"]}
    tool = build_component_tool(cfg, ctx)
    out = await tool.ainvoke({})
    assert "Not permitted" in out


async def test_entitled_user_is_allowed(tmp_path):
    ctx = make_runtime_ctx("t", "p")
    ctx.end_user = {"id": "u2", "entitlements": ["billing:read"]}
    cfg = {"name": "q", "kind": "sql", "connection_url": _sqlite_url(tmp_path, rows=2),
           "query": "SELECT id FROM t ORDER BY id", "required_entitlements": ["billing:read"]}
    tool = materialize_tool(cfg, ctx)
    out = await tool.ainvoke({})
    assert [r["id"] for r in out] == [1, 2]  # entitled -> query actually runs


# --- Finding 3: reliability (rate_limit + cache) reach graphql/sql/code via the shared wrapper ---


async def test_sql_rate_limit_via_wrapper(tmp_path):
    ctx = make_runtime_ctx(f"t_{uuid.uuid4().hex[:6]}", "p")
    cfg = {"name": f"q_{uuid.uuid4().hex[:6]}", "kind": "sql", "connection_url": _sqlite_url(tmp_path),
           "query": "SELECT id FROM t", "rate_limit": {"per_minute": 1}}
    tool = materialize_tool(cfg, ctx)
    await tool.ainvoke({})
    with pytest.raises(RuntimeError, match="rate limit"):
        await tool.ainvoke({})


async def test_sql_cache_via_wrapper(tmp_path):
    url = _sqlite_url(tmp_path, rows=1)
    ctx = make_runtime_ctx("t", "p")
    cfg = {"name": f"q_{uuid.uuid4().hex[:6]}", "kind": "sql", "connection_url": url,
           "query": "SELECT id FROM t ORDER BY id", "cache": {"ttl_seconds": 60}}
    tool = materialize_tool(cfg, ctx)
    first = await tool.ainvoke({})
    # Mutate the DB behind the cache; a cache hit must still return the ORIGINAL rows.
    path = url.split(":///", 1)[1]
    con = sqlite3.connect(path)
    con.execute("INSERT INTO t VALUES (99, 'new')")
    con.commit()
    con.close()
    second = await tool.ainvoke({})
    assert first == second == [{"id": 1}]  # served from cache, not re-queried


# --- Finding 9: kind:"mcp" is creatable (materialize returns a deferred None, does not raise) -----


def test_materialize_mcp_returns_none_instead_of_raising():
    ctx = make_runtime_ctx("t", "p")
    cfg = {"name": "gh", "kind": "mcp", "mcp_client_id": "srv1", "remote_tool_name": "list_issues"}
    assert materialize_tool(cfg, ctx) is None


def test_materialize_unknown_kind_still_raises():
    ctx = make_runtime_ctx("t", "p")
    with pytest.raises(ValueError, match="Unknown tool kind"):
        materialize_tool({"name": "x", "kind": "bogus"}, ctx)


# --- Finding 6: REST multipart encoding + download-size guard ------------------------------------


async def test_multipart_body_encoding_sends_form_data():
    seen = {}

    def handler(req):
        seen["ct"] = req.headers.get("content-type", "")
        seen["body"] = req.content.decode("utf-8", "replace")
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = _rest_cfg()
    cfg["request"] = {
        "method": "POST", "url_template": "https://api.acme.dev/upload", "body_encoding": "multipart",
        "fields": [{"path": "title", "type": "string", "in": "body"}],
    }
    await execute_rest(cfg, {"title": "hello"}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert seen["ct"].startswith("multipart/form-data") and "hello" in seen["body"]


async def test_download_size_guard_marks_oversized_body(monkeypatch):
    monkeypatch.setattr(rest_mod, "_MAX_DOWNLOAD_BYTES", 50)
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"blob": "x" * 500})
    ))
    res = await execute_rest(_rest_cfg(), {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert res["raw"]["error"] == "response_too_large" and res["raw"]["bytes"] > 50


# --- Finding 10: trace I/O redaction defaults ON for a production install --------------------------


def test_trace_redaction_defaults_on_in_production(monkeypatch):
    from forge.config import settings
    from forge.tracing import tool_io

    monkeypatch.setattr(settings, "trace_tool_io_redact", False)
    # Dev + flag off => values pass through.
    monkeypatch.setattr(settings, "environment", "development")
    assert tool_io.redact_headers({"Authorization": "Bearer secret"})["Authorization"] == "Bearer secret"
    # Production + flag off => sensitive values are masked anyway.
    monkeypatch.setattr(settings, "environment", "production")
    masked = tool_io.redact_headers({"Authorization": "Bearer secret"})["Authorization"]
    assert masked != "Bearer secret" and "secret" not in masked
