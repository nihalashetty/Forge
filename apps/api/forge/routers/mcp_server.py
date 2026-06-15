"""Expose a project's tools as an MCP server (JSON-RPC over HTTP).

Implements the core MCP methods (`initialize`, `tools/list`, `tools/call`) so external
MCP clients (Claude Desktop, Cursor, VS Code) can call a Forge project's tools. Auth is a
per-project API key stored in `project.config.mcp_api_key` (required when set; open in the
no-auth dev default). Full Streamable-HTTP/SSE transport is a future enhancement; this
request/response JSON-RPC works with HTTP MCP clients.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from forge.config import settings
from forge.db.base import SessionLocal
from forge.models import Project
from forge.services.runtime import build_compile_context

router = APIRouter(prefix="/v1/mcp", tags=["mcp-server"])


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    parts = auth.split(None, 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None


async def _load_project(project_id: str) -> Project:
    async with SessionLocal() as s:
        proj = (await s.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return proj


def _rpc(rid, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


@router.post("/{project_id}")
async def mcp_rpc(project_id: str, request: Request):
    proj = await _load_project(project_id)
    cfg = proj.config or {}
    api_key = cfg.get("mcp_api_key")
    if api_key:  # enforced when a key is configured
        if _bearer(request) != api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid MCP api key")
    elif settings.auth_required:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "set project.config.mcp_api_key to expose MCP")

    body = await request.json()
    rid = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if method == "initialize":
        return _rpc(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": f"forge-{proj.slug or project_id}", "version": "1.0.0"},
        })

    if method in ("tools/list", "tools/call"):
        async with SessionLocal() as s:
            ctx = await build_compile_context(s, tenant_id=proj.tenant_id, project_id=project_id)

        if method == "tools/list":
            tools = []
            for spec in ctx.tool_specs.values():
                tool = spec["tool"]
                schema = {}
                try:
                    schema = tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object"}
                except Exception:  # noqa: BLE001
                    schema = {"type": "object"}
                tools.append({"name": tool.name, "description": tool.description or "", "inputSchema": schema})
            return _rpc(rid, {"tools": tools})

        # tools/call
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = next((sp["tool"] for sp in ctx.tool_specs.values() if sp["tool"].name == name), None)
        if tool is None:
            return _rpc(rid, error={"code": -32602, "message": f"unknown tool {name!r}"})
        try:
            result = await tool.ainvoke(args)
            return _rpc(rid, {"content": [{"type": "text", "text": str(result)}], "isError": False})
        except Exception as e:  # noqa: BLE001
            return _rpc(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})

    return _rpc(rid, error={"code": -32601, "message": f"method not found: {method}"})
