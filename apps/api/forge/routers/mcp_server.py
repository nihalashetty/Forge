"""Expose a project's tools as an MCP server (JSON-RPC over HTTP).

Implements the core MCP methods (`initialize`, `tools/list`, `tools/call`) so external
MCP clients (Claude Desktop, Cursor, VS Code) can call a Forge project's tools. Auth is a
per-project API key stored in `project.config.mcp_api_key` (required when set; open in the
no-auth dev default). Full Streamable-HTTP/SSE transport is a future enhancement; this
request/response JSON-RPC works with HTTP MCP clients.

Tool sets ("toolsets", GitHub-MCP style): a project's tools can be published as named groups.
  - Base endpoint  POST /v1/mcp/{project_id}            exposes the project's published surface
    (project.config.mcp_published_toolsets: a list of set slugs, or "all"/"default"; unset =>
    every enabled tool, the prior behavior).
  - Per-set endpoint POST /v1/mcp/{project_id}/toolset/{slug} exposes ONLY that set's tools, so
    a client can add one MCP server per toolset. The optional name allow-list
    (project.config.mcp_exposed_tools) still applies as a further filter.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from forge.config import settings
from forge.db.base import SessionLocal
from forge.deps import run_context as parse_run_context
from forge.models import Project, User
from forge.security import TokenError, decode_token
from forge.services.apikeys import ApiKeyService, looks_like_pat
from forge.services.runtime import build_compile_context
from forge.services.tool_sets import ToolSetService
from forge.util.ratelimit import rate_limiter

log = logging.getLogger("forge.routers.mcp_server")

router = APIRouter(prefix="/v1/mcp", tags=["mcp-server"])


def _workflow_tool_name(cfg: dict) -> str | None:
    """The MCP tool name that runs the project's configured workflow, if exposure is enabled
    (project.config.mcp_expose_workflow). Lets an external MCP client invoke a whole Forge
    workflow as a single tool, not just the project's individual tools."""
    if not cfg.get("mcp_expose_workflow"):
        return None
    return str(cfg.get("mcp_workflow_tool_name") or "run_workflow")


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


@router.post("/{project_id}")
async def mcp_rpc(project_id: str, request: Request):
    """Base MCP endpoint: the project's published toolset surface (see module docstring)."""
    return await _handle(project_id, request, toolset_slug=None)


@router.post("/{project_id}/toolset/{slug}")
async def mcp_rpc_toolset(project_id: str, slug: str, request: Request):
    """Scoped MCP endpoint: exposes only the tools in tool set `slug` (GitHub-style toolset)."""
    return await _handle(project_id, request, toolset_slug=slug)


async def _handle(project_id: str, request: Request, *, toolset_slug: str | None):
    proj = await _load_project(project_id)
    cfg = proj.config or {}
    bearer = _bearer(request)
    api_key = cfg.get("mcp_api_key")
    # Authorize the caller and resolve the acting end user (identity). Two accepted credentials:
    #   - the shared per-project mcp_api_key -> authorized, NO per-user identity (server-to-server);
    #   - a Forge session token minted for THIS project (create_session_token) -> authorized AS the
    #     token's end_user, so entitlement gating + {{ctx.*}} act per user. Portable "use anywhere"
    #     identity: any MCP client just sends Authorization: Bearer <token>.
    end_user: dict | None = None
    if api_key and hmac.compare_digest(bearer or "", api_key):
        pass  # shared-key mode (constant-time compare; matches deps.py)
    else:
        # Per-user identity credentials, in order: a project-scoped session token, a per-user
        # Personal Access Token (forge_pat_), then an OAuth 2.1 access token. Each authorizes AS
        # that user.
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
                prm = f"{settings.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource/v1/mcp/{project_id}"
                headers = {"WWW-Authenticate": f'Bearer resource_metadata="{prm}"'}
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers=headers)

    # Rate-limit the exposed MCP surface per project (a single static key is shared by every
    # caller, so this is the real abuse ceiling). Uses the general API per-minute budget.
    if not rate_limiter.allow(f"mcp:{project_id}", rate=settings.api_rate_limit_per_minute, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "MCP rate limit exceeded")

    # Ephemeral per-run context (X-Forge-Context): injected into tools as {{ctx.*}} for on-behalf-of
    # calls (e.g. the end user's downstream session); never persisted or prompted.
    rc = parse_run_context(request)

    body = await request.json()
    rid = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}
    wf_tool = _workflow_tool_name(cfg)

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
        async with SessionLocal() as s:
            ctx = await build_compile_context(
                s, tenant_id=proj.tenant_id, project_id=project_id, end_user=end_user, run_context=rc,
            )
            sets = await ToolSetService.list(s, proj.tenant_id, project_id)

        # Exposed = enabled tools of exposed sets, minus any individually excluded (unticked) tools.
        excluded_ids = {str(x) for x in (cfg.get("mcp_excluded_tools") or [])}
        allow = _exposed_names(ctx, sets, toolset_slug, excluded_ids)

        if method == "tools/list":
            tools = []
            for spec in ctx.tool_specs.values():
                tool = spec["tool"]
                if tool.name not in allow:
                    continue  # only enabled tools of exposed tool sets are published
                schema = {}
                try:
                    schema = tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object"}
                except Exception:  # noqa: BLE001
                    schema = {"type": "object"}
                tools.append({"name": tool.name, "description": tool.description or "", "inputSchema": schema})
            # The whole-workflow-as-one-tool is a project-level surface: expose it on the base
            # endpoint only, not on a per-set (toolset) endpoint.
            if wf_tool and not toolset_slug:
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
        if wf_tool and not toolset_slug and name == wf_tool:
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
                        end_user=end_user,
                    )
                result = await run_service.run_to_completion(
                    run_id=run.id, tenant_id=proj.tenant_id, project_id=project_id, run_context=rc,
                )
                text = result.get("answer") or ""
                return _rpc(rid, {"content": [{"type": "text", "text": text}], "isError": result.get("status") == "error"})
            except Exception:  # noqa: BLE001
                # Don't leak internal error/stack detail to the external MCP client; log it server-side.
                log.exception("mcp workflow run failed (project=%s)", project_id)
                return _rpc(rid, {"content": [{"type": "text", "text": "error: workflow run failed"}], "isError": True})
        if name not in allow:
            return _rpc(rid, error={"code": -32602, "message": f"tool {name!r} is not exposed"})
        tool = next((sp["tool"] for sp in ctx.tool_specs.values() if sp["tool"].name == name), None)
        if tool is None:
            return _rpc(rid, error={"code": -32602, "message": f"unknown tool {name!r}"})
        try:
            result = await tool.ainvoke(args)
            return _rpc(rid, {"content": [{"type": "text", "text": str(result)}], "isError": False})
        except Exception:  # noqa: BLE001
            # Don't leak internal error/stack detail to the external MCP client; log it server-side.
            log.exception("mcp tool invocation failed (project=%s, tool=%s)", project_id, name)
            return _rpc(rid, {"content": [{"type": "text", "text": "error: tool invocation failed"}], "isError": True})

    return _rpc(rid, error={"code": -32601, "message": f"method not found: {method}"})
