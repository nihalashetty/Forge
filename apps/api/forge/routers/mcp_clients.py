"""MCP client CRUD - register external MCP servers a project's tools can consume."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
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


class McpClientPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    disabled_tools: list | None = None  # remote tool names toggled off
    url: str | None = None
    headers_ref: str | None = None


def _out(m: McpClient) -> dict:
    return {"id": m.id, "name": m.name, "transport": m.transport, "url": m.url,
            "command": m.command, "args": m.args, "headers_ref": m.headers_ref,
            "enabled": m.enabled, "disabled_tools": m.disabled_tools or []}


@router.get("")
async def list_clients(project_id: str, session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id)):
    rows = (await session.execute(
        select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.project_id == project_id)
    )).scalars()
    return [_out(m) for m in rows]


@router.post("", status_code=201)
async def create_client(project_id: str, body: McpClientIn, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("editor"))):
    m = McpClient(tenant_id=tenant_id, project_id=project_id, name=body.name, transport=body.transport,
                  url=body.url, command=body.command, args=body.args, headers_ref=body.headers_ref, enabled=body.enabled)
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
    await session.commit()
    await session.refresh(m)
    # Drop the cached connection so running agents pick up the new config (audit F12).
    from forge.tools.mcp import invalidate_client
    invalidate_client(client_id)
    return _out(m)


@router.get("/{client_id}/tools")
async def list_remote_tools(project_id: str, client_id: str, session: AsyncSession = Depends(get_session),
                            tenant_id: str = Depends(current_tenant_id)):
    """Connect to the server and list the tools it exposes - drives the 'pick which to add' UI."""
    from forge.tools.mcp import McpUnavailable, discover_tools

    row = (await session.execute(
        select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.id == client_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp client not found")
    try:
        tools = await discover_tools(row, tenant_id, project_id)
    except McpUnavailable as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface connect/auth errors to the UI, don't 500
        return {"ok": False, "error": f"Could not connect: {e}"}
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
    invalidate_client(client_id)
    return {"ok": True}
