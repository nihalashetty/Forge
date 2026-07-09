"""Service-tool redirect handling.

Two behaviours, both new:
1. A 3xx that is NOT followed (the default) is no longer an empty response - the
   target `location` is captured and surfaced to the model.
2. With `request.follow_redirects` on, redirects are chased SSRF-safely via
   `guarded_request`: the final URL + hop chain are captured, and a hop pointing at
   a blocked (private/metadata) address is refused.
"""

from __future__ import annotations

import types
import uuid

import httpx
import pytest

from forge.tools import rest as rest_mod
from forge.tools.rest import build_rest_tool, execute_rest
from forge.util.ssrf import EgressBlocked, EgressPolicy


def _cfg(**extra) -> dict:
    return {
        "name": f"t_{uuid.uuid4().hex[:8]}",
        "kind": "rest_api",
        "request": {"method": "GET", "url_template": "https://api.acme.dev/go", "fields": []},
        **extra,
    }


def _redirecting_client(location: str, *, status: int = 302) -> httpx.AsyncClient:
    """Mock client: /go -> 3xx(location); the target -> 200 JSON."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/go":
            return httpx.Response(status, headers={"location": location})
        return httpx.Response(200, json={"arrived": True, "path": req.url.path})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- 1. capture without following (default) --------------------------------------

async def test_unfollowed_3xx_captures_location():
    client = _redirecting_client("https://api.acme.dev/v2/final")
    res = await execute_rest(_cfg(), {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()

    assert res["status"] == 302
    assert res["redirect"] is not None
    assert res["redirect"]["followed"] is False
    assert res["redirect"]["location"] == "https://api.acme.dev/v2/final"


async def test_unfollowed_redirect_is_wrapped_for_the_agent():
    """The StructuredTool the agent calls must surface the redirect, not an empty body."""
    client = _redirecting_client("https://api.acme.dev/v2/final")
    rest_mod_select = rest_mod.select_client
    rest_mod.select_client = lambda *a, **k: client  # type: ignore[assignment]
    try:
        ctx = types.SimpleNamespace(tenant_id="t", project_id="p", auth_resolver=None, egress_policy=None)
        tool = build_rest_tool(_cfg(), ctx)
        out = await tool.ainvoke({})
    finally:
        rest_mod.select_client = rest_mod_select  # type: ignore[assignment]
        await client.aclose()

    assert isinstance(out, dict) and "redirect" in out
    assert out["redirect"]["location"] == "https://api.acme.dev/v2/final"


async def test_tool_test_preview_counts_the_redirect_not_an_empty_body():
    """The /test preview (ToolService.test) must reflect what the agent actually gets:
    the {body, redirect} envelope, not the empty 3xx body. Otherwise 'PROJECTED -> MODEL'
    reads "" / 0 tok and looks like nothing reaches the model."""
    from forge.services.tools import ToolService

    client = _redirecting_client("/quote/quotingaddproduct/00015849")
    rest_mod_select = rest_mod.select_client
    rest_mod.select_client = lambda *a, **k: client  # type: ignore[assignment]
    try:
        r = await ToolService.test("t", "p", _cfg(), {})
    finally:
        rest_mod.select_client = rest_mod_select  # type: ignore[assignment]
        await client.aclose()

    assert r["ok"] and r["redirect"]["location"] == "/quote/quotingaddproduct/00015849"
    # raw + projected now carry the observation shape, and the location survives into it.
    assert isinstance(r["projected"], dict)
    assert r["projected"]["redirect"]["location"] == "/quote/quotingaddproduct/00015849"
    assert isinstance(r["raw"], dict) and "redirect" in r["raw"]
    # ...so the meter shows a real cost instead of the old 0 tok.
    assert r["projected_tokens"] > 0 and r["raw_tokens"] > 0


async def test_tool_test_preview_leaves_non_redirect_body_bare():
    """No redirect -> the preview stays the bare body (unchanged), not an envelope."""
    from forge.services.tools import ToolService

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"v": 1})))
    rest_mod_select = rest_mod.select_client
    rest_mod.select_client = lambda *a, **k: client  # type: ignore[assignment]
    try:
        r = await ToolService.test("t", "p", _cfg(), {})
    finally:
        rest_mod.select_client = rest_mod_select  # type: ignore[assignment]
        await client.aclose()

    assert r["ok"] and r["redirect"] is None
    assert r["projected"] == {"v": 1} and r["raw"] == {"v": 1}  # bare body, not wrapped


# --- 3. projecting the redirect location (strip a captured 3xx to just the URL) ----

async def test_projection_can_select_the_redirect_location():
    """`redirect.location` must reach the redirect envelope and collapse the observation to just
    the target URL - so a 3xx can be stripped to the one thing the model needs, cheaply."""
    from forge.services.tools import ToolService

    client = _redirecting_client("/quote/quotingaddproduct/00015854")
    cfg = _cfg(response={"projection_jmespath": "redirect.location"})
    rest_mod_select = rest_mod.select_client
    rest_mod.select_client = lambda *a, **k: client  # type: ignore[assignment]
    try:
        r = await ToolService.test("t", "p", cfg, {})
    finally:
        rest_mod.select_client = rest_mod_select  # type: ignore[assignment]
        await client.aclose()

    assert r["ok"]
    assert r["projected"] == "/quote/quotingaddproduct/00015854"  # just the URL, not the envelope
    assert 0 < r["projected_tokens"] < r["raw_tokens"]  # stripped -> cheaper than the full envelope


async def test_tool_returns_bare_redirect_location_to_the_agent_when_projected():
    """The agent-facing observation (not just the preview) is the bare URL string."""
    client = _redirecting_client("/quote/quotingaddproduct/00015854")
    cfg = _cfg(response={"projection_jmespath": "redirect.location"})
    rest_mod_select = rest_mod.select_client
    rest_mod.select_client = lambda *a, **k: client  # type: ignore[assignment]
    try:
        ctx = types.SimpleNamespace(tenant_id="t", project_id="p", auth_resolver=None, egress_policy=None)
        tool = build_rest_tool(cfg, ctx)
        out = await tool.ainvoke({})
    finally:
        rest_mod.select_client = rest_mod_select  # type: ignore[assignment]
        await client.aclose()

    assert out == "/quote/quotingaddproduct/00015854"


async def test_no_redirect_returns_bare_body_unchanged():
    """A normal 200 must behave exactly as before - no envelope, no redirect key."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"v": 1})))
    res = await execute_rest(_cfg(), {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()

    assert res["redirect"] is None
    from forge.tools.rest import _tool_return

    assert _tool_return(res, _cfg()) == {"v": 1}  # bare body, not wrapped


# --- 2. SSRF-safe following ------------------------------------------------------

async def test_follow_resolves_final_url_and_chain():
    client = _redirecting_client("https://api.acme.dev/v2/final")
    cfg = _cfg()
    cfg["request"]["follow_redirects"] = True
    res = await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()

    assert res["status"] == 200 and res["raw"] == {"arrived": True, "path": "/v2/final"}
    assert res["redirect"]["followed"] is True
    assert res["redirect"]["final_url"] == "https://api.acme.dev/v2/final"
    assert res["redirect"]["chain"] == ["https://api.acme.dev/go"]


async def test_follow_revalidates_each_hop_and_blocks_private_target():
    """A redirect to a blocked address (cloud metadata) must be refused on the hop -
    the SSRF guard is not bypassed by following."""
    client = _redirecting_client("http://169.254.169.254/latest/meta-data/")
    cfg = _cfg()
    cfg["request"]["url_template"] = "https://8.8.8.8/go"  # public literal so the first hop passes
    cfg["request"]["follow_redirects"] = True
    with pytest.raises(EgressBlocked):
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client,
                           egress_policy=EgressPolicy(block_private=True))
    await client.aclose()


def _auth_capturing_client(location: str) -> tuple[httpx.AsyncClient, dict]:
    """Mock client that records the Authorization header seen at each hop."""
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen[str(req.url)] = req.headers.get("authorization")
        if req.url.path == "/go":
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_follow_strips_authorization_on_cross_origin_redirect():
    """SECURITY: a redirect to a DIFFERENT origin must NOT forward the tenant's
    Authorization header - otherwise following redirects exfiltrates credentials."""
    client, seen = _auth_capturing_client("https://evil.example/collect")
    cfg = _cfg()
    cfg["request"]["headers"] = [{"name": "Authorization", "value": "Bearer SECRET-TOKEN"}]
    cfg["request"]["follow_redirects"] = True
    await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()

    assert seen["https://api.acme.dev/go"] == "Bearer SECRET-TOKEN"  # original origin keeps it
    assert seen["https://evil.example/collect"] is None  # foreign origin must NOT receive it


async def test_follow_preserves_authorization_on_same_origin_redirect():
    """A same-origin redirect should keep the Authorization header (e.g. /v1 -> /v2)."""
    client, seen = _auth_capturing_client("https://api.acme.dev/v2/final")
    cfg = _cfg()
    cfg["request"]["headers"] = [{"name": "Authorization", "value": "Bearer SECRET-TOKEN"}]
    cfg["request"]["follow_redirects"] = True
    await execute_rest(cfg, {}, tenant_id="t", project_id="p", client=client)
    await client.aclose()

    assert seen["https://api.acme.dev/v2/final"] == "Bearer SECRET-TOKEN"
