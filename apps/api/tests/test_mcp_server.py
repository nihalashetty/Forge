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


async def test_mcp_exposes_project_tools():
    """Project-level tools (workflow / knowledge / Q&A) are published on the base endpoint when
    their project.config flags are set, and never on a per-set (toolset) endpoint."""
    async with SessionLocal() as s:
        proj = Project(tenant_id="t_mcps3", name="P3", slug="p3", config={
            "mcp_expose_workflow": True, "mcp_workflow_tool_name": "run_it",
            "mcp_expose_knowledge": True, "mcp_expose_faq": True,
        })
        s.add(proj)
        await s.commit()
        await s.refresh(proj)
        pid = proj.id
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # base endpoint: all three project tools are listed (custom workflow tool name honored)
        r = await c.post(f"/v1/mcp/{pid}", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert "run_it" in names
        assert "search_knowledge_base" in names
        assert "lookup_faq" in names

        # per-toolset endpoint: project tools are a whole-project surface, so none of them appear
        r = await c.post(f"/v1/mcp/{pid}/toolset/general", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert not ({"run_it", "search_knowledge_base", "lookup_faq"} & set(names))


async def test_mcp_project_tools_off_by_default():
    """No flags => no project tools (unchanged surface for existing projects)."""
    async with SessionLocal() as s:
        proj = Project(tenant_id="t_mcps4", name="P4", slug="p4", config={})
        s.add(proj)
        await s.commit()
        await s.refresh(proj)
        pid = proj.id
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/v1/mcp/{pid}", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert not ({"run_workflow", "search_knowledge_base", "lookup_faq"} & set(names))


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
