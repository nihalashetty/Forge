"""REST tool reliability config: cache, rate_limit, retry."""

from __future__ import annotations

import uuid

import httpx
import pytest

from forge.tools.rest import execute_rest


def _cfg(**extra) -> dict:
    return {
        "name": f"t_{uuid.uuid4().hex[:8]}",
        "kind": "rest_api",
        "request": {"method": "GET", "url_template": "https://api.acme.dev/v2/ping", "fields": []},
        **extra,
    }


async def test_cache_serves_repeat_get_without_second_call():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"v": calls["n"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = _cfg(cache={"ttl_seconds": 60})
    a = await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    b = await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert calls["n"] == 1 and a["raw"] == b["raw"]  # second served from cache


async def test_rate_limit_blocks_second_call():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    cfg = _cfg(rate_limit={"per_minute": 1})
    await execute_rest(cfg, {}, tenant_id="t_rl", project_id="p", client=client)
    with pytest.raises(RuntimeError):
        await execute_rest(cfg, {}, tenant_id="t_rl", project_id="p", client=client)
    await client.aclose()


async def test_retry_recovers_from_transient_500():
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        return httpx.Response(500 if state["n"] == 1 else 200, json={"ok": state["n"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = _cfg(retry={"max_retries": 2, "initial_delay": 0.001, "jitter": False, "retry_on": ["http_error"]})
    res = await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()
    assert state["n"] == 2 and res["status"] == 200


async def test_no_retry_when_not_configured():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={})))
    cfg = _cfg()  # no retry policy
    with pytest.raises(httpx.HTTPStatusError):
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()


async def test_missing_path_param_raises_clear_error():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    cfg = _cfg()
    cfg["request"]["url_template"] = "https://api.acme.dev/v2/orders/{order_id}"
    with pytest.raises(ValueError, match="order_id"):
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()


def test_cap_payload_truncates_large_only():
    from forge.tools.projection import cap_payload

    assert cap_payload({"a": 1}, 20000) == {"a": 1}  # small passes through unchanged
    big = "x" * 50000
    out = cap_payload(big, 100)
    assert isinstance(out, str) and len(out) < 50000 and "truncated" in out
