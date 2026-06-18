"""MCP tool kind — consume tools from an external MCP server.

Wiring the `mcp` kind unlocks the whole MCP connector ecosystem (GitHub, Slack,
Postgres, Stripe, filesystem, …) without hand-writing each integration. An `McpClient`
row describes the server (http/sse/stdio transport); a tool's config names the
`remote_tool_name` to expose and any `inject_context` keys to fill from the per-user
runtime context (so the model never sets secrets like user_id/api_key).

MCP discovery is async, so MCP tools are loaded by `load_mcp_tools` from the runtime
assembler (not the sync `materialize_tool` path).
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from langchain.tools import ToolRuntime
from sqlalchemy import select

from forge.db.base import SessionLocal
from forge.models import McpClient
from forge.secrets.store import SecretStore

log = logging.getLogger("forge.mcp")

# Cache MultiServerMCPClient instances per mcp_client_id with a TTL so a dead connection or
# an edited server config is eventually re-established without a process restart (audit F12).
# `invalidate_client` drops one entry immediately (called when the McpClient row changes);
# `close_all` is called on shutdown.
_CLIENT_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300.0  # seconds


def invalidate_client(client_id: str) -> None:
    """Drop a cached MCP client so the next run reconnects with the latest config."""
    _CLIENT_CACHE.pop(client_id, None)


async def close_all() -> None:
    """Best-effort close of every cached MCP client (transports/subprocesses) on shutdown."""
    for _, client in list(_CLIENT_CACHE.values()):
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
    _CLIENT_CACHE.clear()


class McpUnavailable(RuntimeError):
    pass


def _require_adapters():
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as e:  # pragma: no cover - optional extra
        raise McpUnavailable(
            "mcp tools need `langchain-mcp-adapters` (pip install -e '.[mcp]')."
        ) from e
    return MultiServerMCPClient


async def _connection_for(client_row: McpClient, tenant_id: str, project_id: str) -> dict:
    transport = client_row.transport or "streamable_http"
    if transport in ("http", "streamable_http"):
        conn: dict[str, Any] = {"url": client_row.url, "transport": "streamable_http"}
    elif transport == "sse":
        conn = {"url": client_row.url, "transport": "sse"}
    elif transport == "stdio":
        args = client_row.args or {}
        conn = {"command": client_row.command, "args": args.get("args", []) if isinstance(args, dict) else args, "transport": "stdio"}
    else:
        raise McpUnavailable(f"unsupported MCP transport {transport!r}")
    if client_row.headers_ref:
        try:
            headers = await SecretStore().read_ref(tenant_id=tenant_id, project_id=project_id, ref=client_row.headers_ref)
            if isinstance(headers, dict):
                conn["headers"] = headers
        except Exception:  # noqa: BLE001 - missing headers secret => connect without
            pass
    return conn


async def _client_and_tools(client_row: McpClient, tenant_id: str, project_id: str):
    MultiServerMCPClient = _require_adapters()
    now = time.monotonic()
    entry = _CLIENT_CACHE.get(client_row.id)
    if entry is None or (now - entry[0]) > _CACHE_TTL:
        conn = await _connection_for(client_row, tenant_id, project_id)
        client = MultiServerMCPClient({client_row.name: conn})
        _CLIENT_CACHE[client_row.id] = (now, client)
    else:
        client = entry[1]
    tools = await client.get_tools()
    return client, tools


async def discover_tools(client_row: McpClient, tenant_id: str, project_id: str) -> list[dict]:
    """List the tools an MCP server exposes — [{name, description}].

    Connects fresh (not via the execution cache) so the result always reflects the
    current McpClient config, and drops any stale cached client so the next run
    reconnects with the latest settings. Raises McpUnavailable / connection errors.
    """
    _CLIENT_CACHE.pop(client_row.id, None)
    MultiServerMCPClient = _require_adapters()
    conn = await _connection_for(client_row, tenant_id, project_id)
    client = MultiServerMCPClient({client_row.name: conn})
    tools = await client.get_tools()
    return [{"name": t.name, "description": (getattr(t, "description", "") or "").strip()} for t in tools]


async def server_tools(client_row: McpClient, tenant_id: str, project_id: str) -> list:
    """Native LangChain tools a server exposes, minus the ones toggled off (disabled_tools).
    Used to attach a whole MCP server's tools to an agent."""
    _client, tools = await _client_and_tools(client_row, tenant_id, project_id)
    disabled = set(getattr(client_row, "disabled_tools", None) or [])
    return [t for t in tools if t.name not in disabled]


def _wrap_with_context_injection(tool, inject_keys: list[str]):
    """Wrap an MCP StructuredTool so `inject_keys` are filled from runtime.context
    (per-user secrets the widget/channel supplies) instead of from the model."""
    from langchain_core.tools import StructuredTool

    underlying = tool

    async def _call(runtime: ToolRuntime = None, **kwargs):  # type: ignore[assignment]
        context = getattr(runtime, "context", None) or {}
        for k in inject_keys or []:
            if k in context:
                kwargs[k] = context[k]
        return await underlying.ainvoke(kwargs)

    return StructuredTool.from_function(
        coroutine=_call, name=underlying.name, description=underlying.description,
        args_schema=underlying.args_schema,
    )


async def load_mcp_tool(cfg: dict, ctx) -> Any:
    """Resolve a single `mcp`-kind tool config to a runnable tool (async)."""
    async with SessionLocal() as s:
        row = (
            await s.execute(
                select(McpClient).where(
                    McpClient.tenant_id == ctx.tenant_id, McpClient.id == cfg["mcp_client_id"]
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise McpUnavailable(f"MCP client {cfg.get('mcp_client_id')!r} not found")
    _client, tools = await _client_and_tools(row, ctx.tenant_id, ctx.project_id)
    name = cfg["remote_tool_name"]
    match = next((t for t in tools if t.name == name), None)
    if match is None:
        raise McpUnavailable(f"remote tool {name!r} not exposed by MCP server {row.name!r}")
    inject = cfg.get("inject_context") or []
    return _wrap_with_context_injection(match, inject) if inject else match


def build_mcp_tool(cfg: dict, ctx):
    # MCP discovery is async; the runtime assembler calls load_mcp_tool instead.
    raise McpUnavailable("mcp tools are loaded asynchronously via load_mcp_tool (runtime assembler).")
