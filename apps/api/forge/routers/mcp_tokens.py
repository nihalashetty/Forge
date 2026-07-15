"""Personal access tokens for a project's MCP server.

A PAT is a per-user, pasteable bearer token (`forge_pat_…`) that authenticates an individual over
`POST /v1/mcp/{project_id}` AS their end_user - the portable "use anywhere" identity for generic MCP
clients (Claude Desktop, Cursor, VS Code). Bound to the current user and scoped to this project; it
is deliberately NOT a general-API credential.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, get_current_user, get_session
from forge.models.entities import ApiKey
from forge.schemas.dto import McpTokenCreate, McpTokenOut
from forge.services.apikeys import ApiKeyService

router = APIRouter(prefix="/v1/projects/{project_id}/mcp-tokens", tags=["mcp-tokens"])


def _out(k: ApiKey, *, token: str | None = None) -> McpTokenOut:
    return McpTokenOut(
        id=k.id, name=k.name, prefix=k.prefix, project_id=k.project_id, status=k.status,
        created_at=k.created_at, last_used_at=k.last_used_at, expires_at=k.expires_at, token=token,
    )


def _require_real_user(user: CurrentUser) -> None:
    # A PAT must bind a real end user; service / api-key / dev-fallback principals have no user id.
    if user.is_fallback or str(user.id).startswith(("apikey:", "service")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "personal tokens require a logged-in user")


@router.get("", response_model=list[McpTokenOut])
async def list_mcp_tokens(project_id: str, session: AsyncSession = Depends(get_session),
                          user: CurrentUser = Depends(get_current_user)):
    _require_real_user(user)
    rows = await ApiKeyService.list_personal(session, user.tenant_id, user.id)
    # Show this project's tokens plus any tenant-wide (unscoped) personal tokens.
    return [_out(k) for k in rows if k.project_id in (None, project_id)]


@router.post("", response_model=McpTokenOut, status_code=201)
async def create_mcp_token(project_id: str, body: McpTokenCreate, session: AsyncSession = Depends(get_session),
                           user: CurrentUser = Depends(get_current_user)):
    _require_real_user(user)
    key, plaintext = await ApiKeyService.create_personal(
        session, tenant_id=user.tenant_id, user_id=user.id,
        name=body.name or "MCP token", project_id=project_id, ttl_days=body.ttl_days,
    )
    return _out(key, token=plaintext)


@router.delete("/{token_id}", status_code=204)
async def revoke_mcp_token(project_id: str, token_id: str, session: AsyncSession = Depends(get_session),
                           user: CurrentUser = Depends(get_current_user)):
    _require_real_user(user)
    if not await ApiKeyService.revoke_personal(session, tenant_id=user.tenant_id, user_id=user.id, key_id=token_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")
