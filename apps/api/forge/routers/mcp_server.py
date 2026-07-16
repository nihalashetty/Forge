"""Expose a project's tools as an MCP server, over two transports that share one core.

  - **Streamable HTTP** (MCP spec 2025-03-26+): the transport native clients (Claude Desktop,
    Cursor, VS Code) speak directly, so they connect WITHOUT an `mcp-remote` proxy bridge. A POST
    is answered with `application/json` or, when the client's `Accept` allows it, a
    `text/event-stream` (SSE) reply; GET/DELETE are handled by the SDK transport. Backed by the
    official `mcp` SDK's StreamableHTTPServerTransport in STATELESS mode — a fresh transport per
    request, no server-side session state — which is exactly Forge's model: every request is
    authenticated on its own and the tool surface is resolved per project + per acting identity.
  - **Legacy JSON-RPC** (request/response over HTTP POST): the original hand-rolled path, kept for
    simple HTTP clients and internal callers that POST plain JSON without the streamable headers.

The transport is chosen per request: a POST whose `Accept` includes `text/event-stream`, or any
GET/DELETE, is served over Streamable HTTP; a plain-JSON POST uses the legacy path. Both resolve the
SAME surface (`_resolve`) and run the SAME dispatch (`_dispatch`), so behavior and tracing are
identical whichever transport a client uses. Auth is a per-project API key stored in
`project.config.mcp_api_key` (required when set; open in the no-auth dev default).

Tool sets ("toolsets", GitHub-MCP style): a project's tools can be published as named groups.
  - Base endpoint  /v1/mcp/{project_id}            exposes the project's published surface
    (project.config.mcp_published_toolsets: a list of set slugs, or "all"/"default"; unset =>
    every enabled tool, the prior behavior).
  - Per-set endpoint /v1/mcp/{project_id}/toolset/{slug} exposes ONLY that set's tools, so
    a client can add one MCP server per toolset. The optional name allow-list
    (project.config.mcp_exposed_tools) still applies as a further filter.

Project tools (base endpoint only, each gated by a project.config flag, published ALONGSIDE the
toolset tools and independent of the toolset allow-list): the whole configured workflow as one
tool (mcp_expose_workflow -> run_workflow), knowledge-base document search (mcp_expose_knowledge
-> search_knowledge_base) and curated Q&A lookup (mcp_expose_faq -> lookup_faq). The two knowledge
tools reuse the SAME builder the agent nodes use, so their behavior and tracing match exactly.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from forge.config import settings
from forge.db.base import SessionLocal
from forge.deps import run_context as parse_run_context
from forge.models import Project, User
from forge.security import TokenError, decode_token
from forge.services.apikeys import ApiKeyService, looks_like_pat
from forge.services.runtime import build_compile_context
from forge.services.tool_sets import ToolSetService
from forge.util.ratelimit import rate_limiter

# Streamable-HTTP transport comes from the optional `mcp` extra. Import it lazily-at-module-load
# behind a guard so this router still registers (legacy JSON-RPC keeps working) when the extra is
# not installed; the streamable branch then returns a clear 501. anyio is a core dependency.
try:
    import anyio
    from mcp import types as mcp_types
    from mcp.server.lowlevel import Server as LowLevelMCPServer
    from mcp.server.streamable_http import StreamableHTTPServerTransport
    from mcp.server.transport_security import TransportSecuritySettings

    # Forge already enforces trusted-hosts + https public URLs at the app layer in prod, so the
    # transport's own DNS-rebinding Host/Origin check (default ON, empty allow-lists => blocks all)
    # is redundant here and would reject every request. Disable it and rely on the app-layer guard.
    _MCP_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    _STREAMABLE_OK = True
except Exception:  # pragma: no cover - exercised only when the `mcp` extra is absent
    _STREAMABLE_OK = False

log = logging.getLogger("forge.routers.mcp_server")

router = APIRouter(prefix="/v1/mcp", tags=["mcp-server"])


def _workflow_tool_name(cfg: dict) -> str | None:
    """The MCP tool name that runs the project's configured workflow, if exposure is enabled
    (project.config.mcp_expose_workflow). Lets an external MCP client invoke a whole Forge
    workflow as a single tool, not just the project's individual tools."""
    if not cfg.get("mcp_expose_workflow"):
        return None
    return str(cfg.get("mcp_workflow_tool_name") or "run_workflow")


def _capability_tools(cfg: dict, ctx, toolset_slug: str | None) -> list:
    """Project-level knowledge tools exposed over MCP, gated by project.config flags (mirrors
    `_workflow_tool_name`): `mcp_expose_knowledge` -> `search_knowledge_base` (RAG over the
    project's documents) and `mcp_expose_faq` -> `lookup_faq` (curated Q&A). Reuses the SAME
    builder the agent nodes use (`build_knowledge_capability_tools`), so behavior + tracing match.

    Base endpoint only: like the workflow tool these are a whole-project surface, not part of any
    one toolset, so a per-set (toolset) endpoint never carries them.
    """
    if toolset_slug:
        return []
    kn: dict = {}
    if cfg.get("mcp_expose_knowledge"):
        kn["rag"] = {"enabled": True}
    if cfg.get("mcp_expose_faq"):
        kn["qa"] = {"enabled": True}
    if not kn:
        return []
    from forge.tools.builtin import build_knowledge_capability_tools

    return build_knowledge_capability_tools(kn, ctx)


def _tool_input_schema(tool) -> dict:
    """The JSON Schema for a StructuredTool's arguments, degrading to an open object on error."""
    try:
        return tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object"}
    except Exception:  # noqa: BLE001
        return {"type": "object"}


def _exposed_names(ctx, sets: list, toolset_slug: str | None, excluded_ids: set[str]) -> set[str]:
    """The flat set of tool NAMES to expose over MCP.

    MCP has no "toolset" primitive - `tools/list` is a flat array - so tool sets are purely a
    Forge-side grouping we flatten here. The surface is the enabled tools of EXPOSED sets, MINUS
    any individually excluded tools: everything is published by default and the operator unticks
    what they don't want (project.config.mcp_excluded_tools = tool ids). `ctx.tool_specs` holds only
    ENABLED tools, so disabled tools drop out automatically. No loose/direct tools: a tool that
    isn't in an exposed set is not published. `toolset_slug` scopes to that one set if exposed.
    """
    if toolset_slug:
        chosen = [x for x in sets if x.slug == toolset_slug and x.exposed]
    else:
        chosen = [x for x in sets if x.exposed]
    names: set[str] = set()
    for st in chosen:
        for tid in ctx.toolset_members.get(st.id, []):
            if tid in excluded_ids:
                continue
            spec = ctx.tool_specs.get(tid)
            if spec is not None:
                names.add(spec["tool"].name)
    return names


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


def _session_end_user(token: str | None, proj: Project) -> dict | None:
    """The verified end_user carried by a Forge session token scoped to THIS project, or None.
    Lets an MCP client authenticate as a specific end user (create_session_token) instead of the
    shared project key, so entitlement gating and {{ctx.*}} injection act on their behalf."""
    if not token:
        return None
    try:
        claims = decode_token(token, expected_type="session")
    except TokenError:
        return None
    if claims.get("pid") != proj.id or claims.get("tid") != proj.tenant_id:
        return None
    eu = claims.get("end_user")
    return eu if isinstance(eu, dict) else None


async def _pat_end_user(token: str | None, proj: Project) -> dict | None:
    """The end_user for a per-user Personal Access Token (forge_pat_) presented to a project's MCP
    server, or None. Lets an individual authenticate any MCP client with a pasteable token; the
    acting identity is the token's owning user, scoped to the token's tenant (+ project if set)."""
    if not token or not looks_like_pat(token):
        return None
    async with SessionLocal() as s:
        key = await ApiKeyService.resolve_personal(s, token)
        if key is None or key.tenant_id != proj.tenant_id:
            return None
        if key.project_id and key.project_id != proj.id:
            return None
        user = (await s.execute(select(User).where(User.id == key.user_id))).scalar_one_or_none()
    if user is None or user.status != "active":
        return None
    return {"id": user.id, "email": user.email, "display_name": user.email, "roles": [user.role]}


async def _oauth_end_user(token: str | None, proj: Project) -> dict | None:
    """The end_user for a Forge-issued OAuth 2.1 MCP access token (see forge.routers.mcp_oauth),
    or None. Validates the token's audience is THIS project's canonical MCP URL (RFC 8707) so a
    token minted for another resource can't be replayed here. Only active when MCP OAuth is on."""
    if not settings.mcp_oauth_enabled or not token:
        return None
    try:
        claims = decode_token(token, expected_type="mcp_access")
    except TokenError:
        return None
    canonical = f"{settings.public_base_url.rstrip('/')}/v1/mcp/{proj.id}"
    res = claims.get("res") or ""  # resource binding (RFC 8707); a custom claim so the shared
    if res != canonical and not res.startswith(canonical):  # JWT decoder doesn't reject on `aud`
        return None
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.id == claims.get("sub")))).scalar_one_or_none()
    if user is None or user.status != "active" or user.tenant_id != proj.tenant_id:
        return None
    return {"id": user.id, "email": user.email, "display_name": user.email, "roles": [user.role]}


def _rpc(rid, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


async def _authorize(request: Request, proj: Project, cfg: dict) -> dict | None:
    """Authorize the caller and resolve the acting end user (identity), raising 401 on failure.
    Shared by both transports. Two accepted credential kinds:
      - the shared per-project mcp_api_key -> authorized, NO per-user identity (server-to-server);
      - a per-user credential (project-scoped session token, forge_pat_ PAT, or OAuth 2.1 access
        token) -> authorized AS that user, so entitlement gating + {{ctx.*}} act on their behalf.
    Portable "use anywhere" identity: any MCP client just sends Authorization: Bearer <token>."""
    bearer = _bearer(request)
    api_key = cfg.get("mcp_api_key")
    if api_key and hmac.compare_digest(bearer or "", api_key):
        return None  # shared-key mode (constant-time compare; matches deps.py)
    end_user = (
        _session_end_user(bearer, proj)
        or await _pat_end_user(bearer, proj)
        or await _oauth_end_user(bearer, proj)
    )
    if end_user is None and (api_key or settings.auth_required or settings.mcp_oauth_enabled):
        detail = "invalid MCP credential" if api_key else "authentication required to use this MCP server"
        headers = None
        if settings.mcp_oauth_enabled:
            # RFC 9728: point the client at this resource's metadata so it can start OAuth.
            prm = f"{settings.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource/v1/mcp/{proj.id}"
            headers = {"WWW-Authenticate": f'Bearer resource_metadata="{prm}"'}
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers=headers)
    return end_user


# --- tool surface + dispatch (shared by both transports) -------------------------------------


@dataclass
class _Surface:
    """The resolved MCP surface for one request: the compile context, the flat allow-list of
    exposed tool names, the optional workflow tool name, the project-level knowledge tools (by
    name), and the toolset scope. Built once per request by `_resolve`."""
    ctx: Any
    allow: set[str]
    wf_tool: str | None
    cap_tools: dict
    toolset_slug: str | None


async def _resolve(proj: Project, cfg: dict, toolset_slug: str | None, end_user: dict | None, rc) -> _Surface:
    """Resolve the project's exposed tool surface for the acting identity + run context."""
    async with SessionLocal() as s:
        ctx = await build_compile_context(
            s, tenant_id=proj.tenant_id, project_id=proj.id, end_user=end_user, run_context=rc,
        )
        sets = await ToolSetService.list(s, proj.tenant_id, proj.id)
    excluded_ids = {str(x) for x in (cfg.get("mcp_excluded_tools") or [])}
    allow = _exposed_names(ctx, sets, toolset_slug, excluded_ids)
    cap_tools = {t.name: t for t in _capability_tools(cfg, ctx, toolset_slug)}
    return _Surface(ctx=ctx, allow=allow, wf_tool=_workflow_tool_name(cfg), cap_tools=cap_tools, toolset_slug=toolset_slug)


def _list_items(surface: _Surface) -> list[dict]:
    """The `tools/list` array: enabled tools of exposed sets, plus the base-endpoint-only
    workflow + knowledge tools. Same shape for both transports."""
    items: list[dict] = []
    for spec in surface.ctx.tool_specs.values():
        tool = spec["tool"]
        if tool.name not in surface.allow:
            continue  # only enabled tools of exposed tool sets are published
        items.append({"name": tool.name, "description": tool.description or "", "inputSchema": _tool_input_schema(tool)})
    # The whole-workflow-as-one-tool and the knowledge tools are a project-level surface: base
    # endpoint only, never a per-set (toolset) endpoint.
    if surface.wf_tool and not surface.toolset_slug:
        items.append({
            "name": surface.wf_tool,
            "description": "Run this project's configured workflow with a text message and return its reply.",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "The user message / input to run the workflow with."}},
                "required": ["message"],
            },
        })
    for t in surface.cap_tools.values():
        items.append({"name": t.name, "description": t.description or "", "inputSchema": _tool_input_schema(t)})
    return items


@dataclass
class _CallResult:
    """Outcome of a tools/call. `unknown` marks a tool that is not exposed / not found: the legacy
    transport renders it as a JSON-RPC -32602 error, while Streamable HTTP (per the MCP spec, which
    reserves protocol errors for malformed requests) renders it as a tool result with isError."""
    text: str
    is_error: bool
    unknown: bool = False


async def _dispatch(request: Request, proj: Project, cfg: dict, surface: _Surface, rc, end_user: dict | None, name: str, args: dict) -> _CallResult:
    """Run one tools/call against the resolved surface. Shared by both transports."""
    # Workflow-as-MCP: run the project's configured workflow to completion and return its answer.
    if surface.wf_tool and not surface.toolset_slug and name == surface.wf_tool:
        from forge.routers.project_run import _configured_workflow
        from forge.services.runs import RunService

        run_service = RunService(
            checkpointer=getattr(request.app.state, "checkpointer", None),
            store=getattr(request.app.state, "store", None),
        )
        message = args.get("message") or args.get("input") or ""
        try:
            async with SessionLocal() as s:
                wf = await _configured_workflow(s, proj.tenant_id, proj.id)
                run = await run_service.create_run(
                    s, tenant_id=proj.tenant_id, project_id=proj.id, workflow_id=wf.id,
                    input={"messages": [{"role": "user", "content": str(message)}]}, source="mcp",
                    end_user=end_user,
                )
            result = await run_service.run_to_completion(
                run_id=run.id, tenant_id=proj.tenant_id, project_id=proj.id, run_context=rc,
            )
            return _CallResult(text=result.get("answer") or "", is_error=result.get("status") == "error")
        except Exception:  # noqa: BLE001
            # Don't leak internal error/stack detail to the external MCP client; log it server-side.
            log.exception("mcp workflow run failed (project=%s)", proj.id)
            return _CallResult(text="error: workflow run failed", is_error=True)
    # Project-level knowledge tools bypass the toolset allow-list (they're a project surface,
    # not a toolset member), so dispatch them before the allow check.
    cap = surface.cap_tools.get(name)
    if cap is not None:
        try:
            result = await cap.ainvoke(args)
            return _CallResult(text=str(result), is_error=False)
        except Exception:  # noqa: BLE001
            log.exception("mcp knowledge tool failed (project=%s, tool=%s)", proj.id, name)
            return _CallResult(text="error: tool invocation failed", is_error=True)
    if name not in surface.allow:
        return _CallResult(text=f"tool {name!r} is not exposed", is_error=True, unknown=True)
    tool = next((sp["tool"] for sp in surface.ctx.tool_specs.values() if sp["tool"].name == name), None)
    if tool is None:
        return _CallResult(text=f"unknown tool {name!r}", is_error=True, unknown=True)
    try:
        result = await tool.ainvoke(args)
        return _CallResult(text=str(result), is_error=False)
    except Exception:  # noqa: BLE001
        # Don't leak internal error/stack detail to the external MCP client; log it server-side.
        log.exception("mcp tool invocation failed (project=%s, tool=%s)", proj.id, name)
        return _CallResult(text="error: tool invocation failed", is_error=True)


# --- Streamable HTTP transport (official mcp SDK, stateless) ---------------------------------


class _ASGIResponse(Response):
    """A FastAPI-returnable Response that hands the raw ASGI channels to an MCP transport. FastAPI
    passes any `Response` instance straight through to Starlette, which then calls it as an ASGI
    app - letting the transport own the reply (single JSON body or a streamed text/event-stream)."""

    def __init__(self, handler):
        self._handler = handler  # async (scope, receive, send) -> None
        self.background = None  # FastAPI reads this on any returned Response before dispatch

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._handler(scope, receive, send)


def _forge_lowlevel_server(request: Request, proj: Project, cfg: dict, *, toolset_slug: str | None, end_user: dict | None, rc):
    """A per-request MCP `Server` whose tools/list + tools/call resolve Forge's surface lazily
    (so `initialize` stays cheap and the surface reflects the caller's identity). One server per
    request keeps the acting identity + run context baked in - no shared/global server state."""
    name = f"forge-{proj.slug or proj.id}" + (f"-{toolset_slug}" if toolset_slug else "")
    server = LowLevelMCPServer(name=name, version="1.0.0")

    @server.list_tools()
    async def _lt() -> list:
        surface = await _resolve(proj, cfg, toolset_slug, end_user, rc)
        return [
            mcp_types.Tool(name=i["name"], description=i["description"], inputSchema=i["inputSchema"])
            for i in _list_items(surface)
        ]

    @server.call_tool(validate_input=False)
    async def _ct(tool_name: str, arguments: dict):
        surface = await _resolve(proj, cfg, toolset_slug, end_user, rc)
        res = await _dispatch(request, proj, cfg, surface, rc, end_user, tool_name, arguments)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=res.text)],
            isError=res.is_error or res.unknown,
        )

    return server


async def _run_streamable(request: Request, proj: Project, server) -> _ASGIResponse:
    """Serve one request over a fresh stateless Streamable-HTTP transport. Mirrors the SDK's own
    stateless path: connect the transport, run the MCP server over its in-memory streams in a task,
    hand the HTTP request to the transport (which replies JSON or SSE), then tear the transport down."""
    transport = StreamableHTTPServerTransport(
        mcp_session_id=None,               # stateless: no session, no cross-request state
        is_json_response_enabled=False,    # let the client's Accept decide JSON vs SSE
        event_store=None,                  # no resumability in stateless mode
        security_settings=_MCP_SECURITY,
    )

    async def _drive(scope: Scope, receive: Receive, send: Send) -> None:
        async def _serve(*, task_status=anyio.TASK_STATUS_IGNORED):
            async with transport.connect() as (read_stream, write_stream):
                task_status.started()
                try:
                    await server.run(read_stream, write_stream, server.create_initialization_options(), stateless=True)
                except Exception:  # noqa: BLE001  # pragma: no cover - server-side crash, logged only
                    log.exception("mcp streamable session crashed (project=%s)", proj.id)

        async with anyio.create_task_group() as tg:
            await tg.start(_serve)
            await transport.handle_request(scope, receive, send)
            await transport.terminate()

    return _ASGIResponse(_drive)


# --- routes -----------------------------------------------------------------------------------


@router.api_route("/{project_id}", methods=["GET", "POST", "DELETE"])
async def mcp_rpc(project_id: str, request: Request):
    """Base MCP endpoint: the project's published toolset surface (see module docstring)."""
    return await _handle(project_id, request, toolset_slug=None)


@router.api_route("/{project_id}/toolset/{slug}", methods=["GET", "POST", "DELETE"])
async def mcp_rpc_toolset(project_id: str, slug: str, request: Request):
    """Scoped MCP endpoint: exposes only the tools in tool set `slug` (GitHub-style toolset)."""
    return await _handle(project_id, request, toolset_slug=slug)


def _wants_stream(request: Request) -> bool:
    """A request routes to Streamable HTTP if it's a GET/DELETE (transport-level methods) or a POST
    that accepts an SSE reply; a plain-JSON POST stays on the legacy request/response path."""
    if request.method in ("GET", "DELETE"):
        return True
    return "text/event-stream" in (request.headers.get("accept") or "").lower()


async def _handle(project_id: str, request: Request, *, toolset_slug: str | None):
    proj = await _load_project(project_id)
    cfg = proj.config or {}
    end_user = await _authorize(request, proj, cfg)

    # Rate-limit the exposed MCP surface per project (a single static key is shared by every
    # caller, so this is the real abuse ceiling). Uses the general API per-minute budget.
    if not rate_limiter.allow(f"mcp:{project_id}", rate=settings.api_rate_limit_per_minute, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "MCP rate limit exceeded")

    # Ephemeral per-run context (X-Forge-Context): injected into tools as {{ctx.*}} for on-behalf-of
    # calls (e.g. the end user's downstream session); never persisted or prompted. Header-only, so
    # it is safe to read before the streamable transport consumes the request body.
    rc = parse_run_context(request)

    # Streamable HTTP (native MCP clients: Claude Desktop / Cursor / VS Code).
    if _wants_stream(request):
        if not _STREAMABLE_OK:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED,
                "Streamable HTTP transport requires the 'mcp' extra (pip install -e '.[mcp]').",
            )
        server = _forge_lowlevel_server(request, proj, cfg, toolset_slug=toolset_slug, end_user=end_user, rc=rc)
        return await _run_streamable(request, proj, server)

    # Legacy request/response JSON-RPC over HTTP POST.
    body = await request.json()
    rid = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if method == "initialize":
        name = f"forge-{proj.slug or project_id}"
        if toolset_slug:
            name = f"{name}-{toolset_slug}"
        return _rpc(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": name, "version": "1.0.0"},
        })

    if method in ("tools/list", "tools/call"):
        surface = await _resolve(proj, cfg, toolset_slug, end_user, rc)
        if method == "tools/list":
            return _rpc(rid, {"tools": _list_items(surface)})
        # tools/call
        name = params.get("name")
        args = params.get("arguments") or {}
        res = await _dispatch(request, proj, cfg, surface, rc, end_user, name, args)
        if res.unknown:
            return _rpc(rid, error={"code": -32602, "message": res.text})
        return _rpc(rid, {"content": [{"type": "text", "text": res.text}], "isError": res.is_error})

    return _rpc(rid, error={"code": -32601, "message": f"method not found: {method}"})
