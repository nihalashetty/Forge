"""Forge-as-an-MCP-server: initialize / tools/list / tools/call over JSON-RPC."""

from __future__ import annotations

import httpx

from forge.db.base import SessionLocal
from forge.main import create_app
from forge.models import Project, Tool


async def _seed_project_with_tool(slug="mcp-proj") -> str:
    from forge.services.tool_sets import ToolSetService

    async with SessionLocal() as s:
        proj = Project(tenant_id="t_mcps", name="MCP Proj", slug=slug, config={})
        s.add(proj)
        await s.flush()
        tool = Tool(tenant_id="t_mcps", project_id=proj.id, name="calculator", kind="builtin",
                    config={"builtin": "calculator", "description": "Evaluate arithmetic."})
        s.add(tool)
        await s.commit()
        await s.refresh(proj)
        await s.refresh(tool)
        # The MCP surface is the enabled tools of EXPOSED tool sets, so publish via a set.
        await ToolSetService.create(s, "t_mcps", proj.id, name="General", tool_ids=[tool.id])
        return proj.id


async def test_mcp_initialize_and_list_and_call():
    pid = await _seed_project_with_tool()
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # initialize
        r = await c.post(f"/v1/mcp/{pid}", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"].startswith("forge-")

        # tools/list exposes the project's tools
        r = await c.post(f"/v1/mcp/{pid}", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert "calculator" in names

        # tools/call runs it
        r = await c.post(f"/v1/mcp/{pid}", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "calculator", "arguments": {"expression": "6*7"}},
        })
        body = r.json()["result"]
        assert body["isError"] is False and "42" in body["content"][0]["text"]


async def test_mcp_requires_key_when_configured():
    async with SessionLocal() as s:
        proj = Project(tenant_id="t_mcps2", name="P2", slug="p2", config={"mcp_api_key": "secret-key"})
        s.add(proj)
        await s.commit()
        await s.refresh(proj)
        pid = proj.id
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # no key -> 401
        r = await c.post(f"/v1/mcp/{pid}", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status_code == 401
        # correct key -> ok
        r = await c.post(f"/v1/mcp/{pid}", headers={"Authorization": "Bearer secret-key"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status_code == 200
