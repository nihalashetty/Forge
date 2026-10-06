"""The bundled keyless example uses the ordinary install and MCP execution paths."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from forge.connectors.catalog import list_examples, list_manifests
from forge.connectors.install import ConnectorInstaller
from forge.db.base import SessionLocal
from forge.models import McpClient, Tool
from forge.nodes.data import tool_call_factory
from forge.secrets.store import SecretStore
from forge.services.runtime import build_compile_context
from forge.tools import mcp as mcp_mod


async def test_parallel_example_installs_and_executes_without_credentials(monkeypatch):
    manifest, _ = next(pair for pair in list_examples() if pair[0].slug == "parallel-search")
    assert manifest.slug not in {m.slug for m in list_manifests()}
    assert manifest.auth.kind == "none"
    assert not manifest.auth.setup
    seen = []

    async def send(client, request, **kwargs):
        seen.append(request)
        if request.method != "POST":
            return httpx.Response(200, request=request)
        payload = json.loads(request.content)
        method = payload["method"]
        if "id" not in payload:
            return httpx.Response(202, request=request)
        if method == "initialize":
            result = {"protocolVersion": payload["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [
                {"name": name, "description": name,
                 "inputSchema": {"type": "object", "properties": {
                     key: {"type": "array", "items": {"type": "string"}}
                 }, "required": [key]}}
                for name, key in [("web_search", "search_queries"), ("web_fetch", "urls")]
            ]}
        else:
            assert method == "tools/call"
            result = {"content": [{"type": "text", "text": "Python docs: https://docs.python.org/3/"}],
                      "isError": False}
        return httpx.Response(200, request=request,
                              json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    tenant, project = "t_parallel", "p_parallel"
    try:
        async with SessionLocal() as session:
            installed = await ConnectorInstaller().install(
                session, tenant, project, manifest, source="custom",
            )
            assert installed.status == "connected"
            assert installed.auth_provider_id is None
            assert installed.created_secret_names == []
            tools = list((await session.execute(select(Tool).where(
                Tool.id.in_(installed.created_tool_ids)
            ))).scalars())
        assert {t.config["remote_tool_name"] for t in tools} == {"web_search", "web_fetch"}
        async with SessionLocal() as session:
            ctx = await build_compile_context(session, tenant_id=tenant, project_id=project)
        granted = ctx.tools_for(ctx.resolve_tool_ids([], [installed.tool_set_id]))
        assert {tool.name for tool in granted} == {"web_search", "web_fetch"}
        for row in tools:
            tool = ctx.tool_registry[row.id]
            args = {"search_queries": ["Python docs"]} if tool.name == "web_search" else {
                "urls": ["https://docs.python.org/3/"]
            }
            node = tool_call_factory({"tool_id": row.id, "input_mapping": {k: k for k in args},
                                      "output_key": "result"}, ctx)
            assert "docs.python.org" in str(await node(args))
        assert seen
        assert all(r.headers["User-Agent"].startswith("Forge/") for r in seen)
        assert all("Authorization" not in r.headers for r in seen)
        methods = [json.loads(r.content)["method"] for r in seen if r.method == "POST"]
        assert "tools/list" in methods and methods.count("tools/call") == 2
    finally:
        await mcp_mod.close_all()


@pytest.mark.parametrize("transport", ["streamable_http", "http", "sse"])
@pytest.mark.parametrize("header_name", ["User-Agent", "user-agent"])
async def test_mcp_saved_headers_override_default_user_agent(transport, header_name):
    tenant, project = f"t_headers_{transport}_{header_name}", "p_headers"
    async with SessionLocal() as session:
        await SecretStore().write(session, tenant_id=tenant, project_id=project,
                                  name="headers", value={header_name: "MyClient/1", "X-Test": "kept"})
        await session.commit()
    row = McpClient(transport=transport, url="https://search.parallel.ai/mcp",
                    headers_ref="secret://proj/headers")
    conn = await mcp_mod._connection_for(row, tenant, project)
    assert conn["headers"] == {header_name: "MyClient/1", "X-Test": "kept"}
