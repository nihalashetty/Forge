"""MCP client-side auth: an Auth Provider attached to an external MCP server.

This is what makes an OAuth-protected remote MCP server (mcp.slack.com and friends) reachable.
The properties that matter, and are easy to get wrong:

  * provider headers are applied per REQUEST, so a refreshed token lands without reconnecting,
  * a 401 forces exactly one re-resolve and retry (not a loop),
  * a PER-USER provider gets its OWN pooled connection per end user - the bug where one user's
    authenticated session is handed to the next caller is the whole reason the cache key has a
    user-dims suffix.
"""

from __future__ import annotations

import httpx
import pytest

from forge.db.base import SessionLocal
from forge.models import McpClient
from forge.services.auth_providers import AuthProviderService
from forge.tools import mcp as mcp_mod


async def _provider(tenant, project, *, per_user: bool, name="srv"):
    async with SessionLocal() as s:
        cfg = {"token_ref": "secret://proj/tok", "header_name": "Authorization", "prefix": "Bearer "}
        if per_user:
            cfg["per_user_context_keys"] = ["end_user_id"]
        ap = await AuthProviderService.create(s, tenant, project, name=name, kind="bearer", config=cfg)
        return ap.id


async def _client(tenant, project, ap_id=None, transport="streamable_http"):
    async with SessionLocal() as s:
        row = McpClient(tenant_id=tenant, project_id=project, name="srv", transport=transport,
                        url="https://mcp.example/mcp", auth_provider_id=ap_id)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row


class _StubResolver:
    """Stands in for AuthResolver: records each resolve and hands back a token."""

    def __init__(self, token="tok-1"):
        self.calls: list[dict] = []
        self.token = token

    async def resolve(self, *, tenant_id, project_id, provider_id, context=None, force=False):
        self.calls.append({"context": dict(context or {}), "force": force})
        from forge.auth_providers.resolver import ResolvedAuth
        return ResolvedAuth(headers={"Authorization": f"Bearer {self.token}"})


async def test_no_auth_provider_means_no_auth_on_the_connection():
    row = await _client("t_ma_n", "p_ma_n")
    conn = await mcp_mod._connection_for(row, "t_ma_n", "p_ma_n")
    assert "auth" not in conn


async def test_auth_provider_attaches_an_httpx_auth_to_the_connection():
    tenant, project = "t_ma_a", "p_ma_a"
    ap_id = await _provider(tenant, project, per_user=False)
    row = await _client(tenant, project, ap_id)
    conn = await mcp_mod._connection_for(row, tenant, project)
    assert isinstance(conn.get("auth"), httpx.Auth)


async def test_stdio_transport_never_gets_http_auth():
    """A provider on a stdio server is a config mistake; half-applying it would be worse."""
    tenant, project = "t_ma_s", "p_ma_s"
    ap_id = await _provider(tenant, project, per_user=False)
    row = await _client(tenant, project, ap_id, transport="stdio")
    row.command = "echo"
    from forge.config import settings
    settings.enable_mcp_stdio = True
    try:
        conn = await mcp_mod._connection_for(row, tenant, project)
    finally:
        settings.enable_mcp_stdio = False
    assert "auth" not in conn


async def test_provider_auth_applies_headers_per_request():
    resolver = _StubResolver()
    auth = mcp_mod._ProviderAuth(resolver, tenant_id="t", project_id="p", provider_id="ap", context={})
    request = httpx.Request("POST", "https://mcp.example/mcp")

    flow = auth.async_auth_flow(request)
    sent = await flow.__anext__()
    assert sent.headers["Authorization"] == "Bearer tok-1"

    # A rotated token is picked up on the NEXT request without rebuilding the connection.
    resolver.token = "tok-2"
    request2 = httpx.Request("POST", "https://mcp.example/mcp")
    flow2 = auth.async_auth_flow(request2)
    sent2 = await flow2.__anext__()
    assert sent2.headers["Authorization"] == "Bearer tok-2"


async def test_401_forces_one_refresh_and_retries_once():
    resolver = _StubResolver()
    auth = mcp_mod._ProviderAuth(resolver, tenant_id="t", project_id="p", provider_id="ap", context={})
    request = httpx.Request("POST", "https://mcp.example/mcp")

    flow = auth.async_auth_flow(request)
    await flow.__anext__()
    resolver.token = "fresh"
    retried = await flow.asend(httpx.Response(401, request=request))
    assert retried.headers["Authorization"] == "Bearer fresh"
    assert resolver.calls[-1]["force"] is True, "the retry must bypass the resolver's cache"

    # Exactly one retry: a second 401 ends the flow rather than looping.
    with pytest.raises(StopAsyncIteration):
        await flow.asend(httpx.Response(401, request=request))


async def test_success_does_not_re_resolve():
    resolver = _StubResolver()
    auth = mcp_mod._ProviderAuth(resolver, tenant_id="t", project_id="p", provider_id="ap", context={})
    request = httpx.Request("POST", "https://mcp.example/mcp")
    flow = auth.async_auth_flow(request)
    await flow.__anext__()
    with pytest.raises(StopAsyncIteration):
        await flow.asend(httpx.Response(200, request=request))
    assert len(resolver.calls) == 1


async def test_shared_provider_pools_one_connection_for_everyone():
    tenant, project = "t_ma_sh", "p_ma_sh"
    ap_id = await _provider(tenant, project, per_user=False)
    row = await _client(tenant, project, ap_id)
    _a, s1 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u1"})
    _b, s2 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u2"})
    assert s1 == s2 == ""


async def test_per_user_provider_gets_a_distinct_connection_per_end_user():
    tenant, project = "t_ma_pu", "p_ma_pu"
    ap_id = await _provider(tenant, project, per_user=True)
    row = await _client(tenant, project, ap_id)
    _a, s1 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u1"})
    _b, s2 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u2"})
    _c, s3 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u1"})
    assert s1 and s2 and s1 != s2, "two end users must not share one authenticated MCP session"
    assert s1 == s3, "the same end user must reuse their own pooled session"


async def test_missing_provider_degrades_to_no_auth_rather_than_failing():
    tenant, project = "t_ma_m", "p_ma_m"
    row = await _client(tenant, project, "does-not-exist")
    auth, suffix = await mcp_mod._auth_for(row, tenant, project, {})
    assert auth is None and suffix == ""


async def test_invalidate_client_drops_every_per_user_variant():
    mcp_mod._CLIENT_CACHE.clear()
    mcp_mod._CLIENT_CACHE["cid"] = (0.0, object())
    mcp_mod._CLIENT_CACHE["cid::abc"] = (0.0, object())
    mcp_mod._CLIENT_CACHE["cid::def"] = (0.0, object())
    mcp_mod._CLIENT_CACHE["other"] = (0.0, object())
    mcp_mod.invalidate_client("cid")
    assert set(mcp_mod._CLIENT_CACHE) == {"other"}


def test_auth_context_from_puts_end_user_last():
    """end_user_id is authoritative: a caller-injected run_context value must not shadow it,
    or one user could resolve another's stored credential."""

    class _Ctx:
        run_context = {"end_user_id": "spoofed", "csrf": "x"}
        end_user = {"id": "real-user", "email": "real@acme.com"}

    out = mcp_mod.auth_context_from(_Ctx())
    assert out["end_user_id"] == "real-user"
    assert out["csrf"] == "x"
