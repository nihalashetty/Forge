"""MCP client CRUD - register external MCP servers a project's tools can consume."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_current_user, get_session, require_role
from forge.models import McpClient

router = APIRouter(prefix="/v1/projects/{project_id}/mcp-clients", tags=["mcp-clients"])


class McpClientIn(BaseModel):
    name: str
    transport: str = "streamable_http"  # streamable_http | sse | stdio
    url: str | None = None
    command: str | None = None
    args: dict = {}
    headers_ref: str | None = None
    enabled: bool = True
    # Attach an Auth Provider instead of (or as well as) a static header secret - this is how a
    # server behind OAuth is reached, with refresh and optional per-user credentials.
    auth_provider_id: str | None = None


class McpClientPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    disabled_tools: list | None = None  # remote tool names toggled off
    url: str | None = None
    headers_ref: str | None = None
    auth_provider_id: str | None = None


def _out(m: McpClient) -> dict:
    return {"id": m.id, "name": m.name, "transport": m.transport, "url": m.url,
            "command": m.command, "args": m.args, "headers_ref": m.headers_ref,
            "enabled": m.enabled, "disabled_tools": m.disabled_tools or [],
            "auth_provider_id": m.auth_provider_id}


@router.get("")
async def list_clients(project_id: str, session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id)):
    """Registered MCP servers, each flagged with the connector that owns it (if any).

    A connector-owned server already surfaces its tools as a tool set, so anything picking tools
    for an agent should offer the SET and skip the server - listing both makes one integration
    look like two, and granting either does the same thing.
    """
    from forge.models import ConnectorInstall

    rows = list((await session.execute(
        select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.project_id == project_id)
    )).scalars())
    owned = {
        i.mcp_client_id: i.slug
        for i in (await session.execute(
            select(ConnectorInstall).where(
                ConnectorInstall.tenant_id == tenant_id, ConnectorInstall.project_id == project_id,
            )
        )).scalars()
        if i.mcp_client_id
    }
    return [{**_out(m), "connector_slug": owned.get(m.id)} for m in rows]


@router.post("", status_code=201)
async def create_client(project_id: str, body: McpClientIn, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("editor"))):
    m = McpClient(tenant_id=tenant_id, project_id=project_id, name=body.name, transport=body.transport,
                  url=body.url, command=body.command, args=body.args, headers_ref=body.headers_ref,
                  enabled=body.enabled, auth_provider_id=body.auth_provider_id)
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return _out(m)


@router.patch("/{client_id}")
async def update_client(project_id: str, client_id: str, body: McpClientPatch, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("editor"))):
    m = (await session.execute(
        select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.id == client_id)
    )).scalar_one_or_none()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp client not found")
    if body.name is not None:
        m.name = body.name
    if body.enabled is not None:
        m.enabled = body.enabled
    if body.disabled_tools is not None:
        m.disabled_tools = body.disabled_tools
    if body.url is not None:
        m.url = body.url
    if body.headers_ref is not None:
        m.headers_ref = body.headers_ref
    if body.auth_provider_id is not None:
        # "" clears the attachment (back to anonymous / headers_ref only).
        m.auth_provider_id = body.auth_provider_id or None
    await session.commit()
    await session.refresh(m)
    # Drop the cached connection so running agents pick up the new config (audit F12).
    from forge.tools.mcp import invalidate_client
    await invalidate_client(client_id)
    return _out(m)


@router.get("/{client_id}/tools")
async def list_remote_tools(project_id: str, client_id: str, session: AsyncSession = Depends(get_session),
                            tenant_id: str = Depends(current_tenant_id),
                            user: CurrentUser = Depends(get_current_user)):
    """Connect to the server and list the tools it exposes - drives the 'pick which to add' UI."""
    from forge.tools.mcp import McpUnavailable, describe_mcp_error, discover_tools

    row = (await session.execute(
        select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.id == client_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp client not found")
    # A server behind a per-user auth provider only has THIS caller's token to answer with.
    try:
        tools = await discover_tools(row, tenant_id, project_id, {"end_user_id": str(user.id)})
    except McpUnavailable as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface connect/auth errors to the UI, don't 500
        # Unwrapped, an anyio task group reports every failure as "unhandled errors in a
        # TaskGroup", which hides the 401/DNS/TLS cause the user needs to see.
        return {"ok": False, "error": f"Could not connect: {describe_mcp_error(e)}"}
    return {"ok": True, "tools": tools}


@router.delete("/{client_id}")
async def delete_client(project_id: str, client_id: str, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("editor"))):
    m = (await session.execute(
        select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.id == client_id)
    )).scalar_one_or_none()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp client not found")
    await session.delete(m)
    await session.commit()
    from forge.tools.mcp import invalidate_client
    await invalidate_client(client_id)
    return {"ok": True}
