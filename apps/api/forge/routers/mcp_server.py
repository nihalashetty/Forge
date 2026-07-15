"""Expose a project's tools as an MCP server (JSON-RPC over HTTP).

Implements the core MCP methods (`initialize`, `tools/list`, `tools/call`) so external
MCP clients (Claude Desktop, Cursor, VS Code) can call a Forge project's tools. Auth is a
per-project API key stored in `project.config.mcp_api_key` (required when set; open in the
no-auth dev default). Full Streamable-HTTP/SSE transport is a future enhancement; this
request/response JSON-RPC works with HTTP MCP clients.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from forge.config import settings
from forge.db.base import SessionLocal
from forge.models import Project
from forge.services.runtime import build_compile_context
from forge.util.ratelimit import rate_limiter

log = logging.getLogger("forge.routers.mcp_server")

router = APIRouter(prefix="/v1/mcp", tags=["mcp-server"])


def _exposed_tool_names(cfg: dict) -> set[str] | None:
    """Optional per-project allow-list of tool names to expose over MCP (project.config
    .mcp_exposed_tools). None => expose all enabled tools (prior behavior). Lets an operator
    publish only safe tools instead of every tool incl. SQL/code."""
    names = cfg.get("mcp_exposed_tools")
    if isinstance(names, list) and names:
        return {str(n) for n in names}
    return None


def _workflow_tool_name(cfg: dict) -> str | None:
    """The MCP tool name that runs the project's configured workflow, if exposure is enabled
    (project.config.mcp_expose_workflow). Lets an external MCP client invoke a whole Forge
    workflow as a single tool, not just the project's individual tools."""
    if not cfg.get("mcp_expose_workflow"):
        return None
    return str(cfg.get("mcp_workflow_tool_name") or "run_workflow")


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
        # Constant-time compare so a wrong key can't be recovered via timing (matches deps.py).
        if not hmac.compare_digest(_bearer(request) or "", api_key):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid MCP api key")
    elif settings.auth_required:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "set project.config.mcp_api_key to expose MCP")

    # Rate-limit the exposed MCP surface per project (a single static key is shared by every
    # caller, so this is the real abuse ceiling). Uses the general API per-minute budget.
    if not rate_limiter.allow(f"mcp:{project_id}", rate=settings.api_rate_limit_per_minute, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "MCP rate limit exceeded")

    body = await request.json()
    rid = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}
    allow = _exposed_tool_names(cfg)
    wf_tool = _workflow_tool_name(cfg)

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
                if allow is not None and tool.name not in allow:
                    continue  # not on the project's exposed-tools allow-list
                schema = {}
                try:
                    schema = tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object"}
                except Exception:  # noqa: BLE001
                    schema = {"type": "object"}
                tools.append({"name": tool.name, "description": tool.description or "", "inputSchema": schema})
            if wf_tool:
                # Expose the project's configured workflow as one MCP tool (Feature: workflow-as-MCP).
                tools.append({
                    "name": wf_tool,
                    "description": "Run this project's configured workflow with a text message and return its reply.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"message": {"type": "string", "description": "The user message / input to run the workflow with."}},
                        "required": ["message"],
                    },
                })
            return _rpc(rid, {"tools": tools})

        # tools/call
        name = params.get("name")
        args = params.get("arguments") or {}
        # Workflow-as-MCP: run the project's configured workflow to completion and return its answer.
        if wf_tool and name == wf_tool:
            from forge.routers.project_run import _configured_workflow
            from forge.services.runs import RunService

            run_service = RunService(
                checkpointer=getattr(request.app.state, "checkpointer", None),
                store=getattr(request.app.state, "store", None),
            )
            message = args.get("message") or args.get("input") or ""
            try:
                async with SessionLocal() as s:
                    wf = await _configured_workflow(s, proj.tenant_id, project_id)
                    run = await run_service.create_run(
                        s, tenant_id=proj.tenant_id, project_id=project_id, workflow_id=wf.id,
                        input={"messages": [{"role": "user", "content": str(message)}]}, source="mcp",
                    )
                result = await run_service.run_to_completion(run_id=run.id, tenant_id=proj.tenant_id, project_id=project_id)
                text = result.get("answer") or ""
                return _rpc(rid, {"content": [{"type": "text", "text": text}], "isError": result.get("status") == "error"})
            except Exception:  # noqa: BLE001
                # Don't leak internal error/stack detail to the external MCP client; log it server-side.
                log.exception("mcp workflow run failed (project=%s)", project_id)
                return _rpc(rid, {"content": [{"type": "text", "text": "error: workflow run failed"}], "isError": True})
        if allow is not None and name not in allow:
            return _rpc(rid, error={"code": -32602, "message": f"tool {name!r} is not exposed"})
        tool = next((sp["tool"] for sp in ctx.tool_specs.values() if sp["tool"].name == name), None)
        if tool is None:
            return _rpc(rid, error={"code": -32602, "message": f"unknown tool {name!r}"})
        try:
            result = await tool.ainvoke(args)
            return _rpc(rid, {"content": [{"type": "text", "text": str(result)}], "isError": False})
        except Exception as e:  # noqa: BLE001
            return _rpc(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})

    return _rpc(rid, error={"code": -32601, "message": f"method not found: {method}"})
