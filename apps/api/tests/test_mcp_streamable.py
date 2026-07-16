"""Forge-as-an-MCP-server over the Streamable-HTTP transport.

The headline test drives the endpoint with the REAL `mcp` SDK client (the same protocol Claude
Desktop / Cursor / VS Code speak), routed at the in-process ASGI app — proving a native client can
initialize, list, and call tools with no `mcp-remote` proxy bridge. The rest pin the HTTP-level
contract: a POST that accepts SSE gets a `text/event-stream` reply, a plain-JSON POST still gets the
legacy JSON response, and the per-project auth applies to the streaming transport too.
"""

from __future__ import annotations

import json

import httpx

from forge.db.base import SessionLocal
from forge.main import create_app
from forge.models import Project, Tool


async def _seed_project_with_tool(tenant="t_stream", slug="mcp-stream", config=None) -> str:
    from forge.services.tool_sets import ToolSetService

    async with SessionLocal() as s:
        proj = Project(tenant_id=tenant, name="Stream Proj", slug=slug, config=config or {})
        s.add(proj)
        await s.flush()
        tool = Tool(tenant_id=tenant, project_id=proj.id, name="calculator", kind="builtin",
                    config={"builtin": "calculator", "description": "Evaluate arithmetic."})
        s.add(tool)
        await s.commit()
        await s.refresh(proj)
        await s.refresh(tool)
        await ToolSetService.create(s, tenant, proj.id, name="General", tool_ids=[tool.id])
        return proj.id


def _asgi_httpx_factory(app):
    """An httpx client factory (the shape `streamablehttp_client` expects) that routes the MCP
    client's real HTTP traffic through the in-process ASGI app instead of the network."""

    def make(*, headers=None, timeout=None, auth=None, **_):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers=headers, timeout=timeout, auth=auth,
        )

    return make


async def test_streamable_real_mcp_client_end_to_end():
    """A real MCP SDK client initializes, lists, and calls a tool over Streamable HTTP."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    pid = await _seed_project_with_tool()
    app = create_app()
    url = f"http://test/v1/mcp/{pid}"

    async with streamablehttp_client(url, httpx_client_factory=_asgi_httpx_factory(app)) as (read, write, _sid):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name.startswith("forge-")

            tools = await session.list_tools()
            assert "calculator" in [t.name for t in tools.tools]

            res = await session.call_tool("calculator", {"expression": "6*7"})
            assert res.isError is False
            assert "42" in res.content[0].text


def _parse_sse_json(body: str) -> dict:
    """Pull the JSON-RPC payload out of a single-message `text/event-stream` response."""
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no SSE data frame in response:\n{body}")


async def test_streamable_post_negotiates_sse():
    """A POST that accepts text/event-stream is answered with an SSE-framed JSON-RPC reply."""
    pid = await _seed_project_with_tool(tenant="t_stream2", slug="mcp-stream2")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            f"/v1/mcp/{pid}",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"},
            }},
        )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    payload = _parse_sse_json(r.text)
    assert payload["result"]["serverInfo"]["name"].startswith("forge-")


async def test_plain_json_post_still_uses_legacy_json():
    """A POST WITHOUT an SSE Accept stays on the legacy request/response path (application/json)."""
    pid = await _seed_project_with_tool(tenant="t_stream3", slug="mcp-stream3")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/v1/mcp/{pid}", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    assert "calculator" in [t["name"] for t in r.json()["result"]["tools"]]


async def test_streamable_transport_enforces_project_key():
    """The per-project mcp_api_key gates the streaming transport, not just the legacy path."""
    pid = await _seed_project_with_tool(tenant="t_stream4", slug="mcp-stream4", config={"mcp_api_key": "sk-stream"})
    app = create_app()
    sse = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"},
        }}
        # no key -> 401 even on the streamable transport
        r = await c.post(f"/v1/mcp/{pid}", headers=sse, json=init)
        assert r.status_code == 401
        # correct key -> streamed 200
        r = await c.post(f"/v1/mcp/{pid}", headers={**sse, "Authorization": "Bearer sk-stream"}, json=init)
        assert r.status_code == 200 and "text/event-stream" in r.headers["content-type"]
