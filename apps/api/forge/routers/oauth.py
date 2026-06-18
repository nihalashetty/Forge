"""3-legged OAuth (authorization_code) connect flow for auth providers.

Flow: the console calls `/oauth/start` to get the provider's authorize URL (carrying a
short-lived signed `state`); the user grants access and the provider redirects the
browser to `/v1/oauth/callback`, which validates `state`, exchanges the code for tokens,
and stores them as a secret. The AuthResolver then auto-refreshes on expiry.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.auth_providers.resolver import AuthResolver
from forge.config import settings
from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
from forge.models import AuthProvider
from forge.secrets.store import SecretNotFound, SecretStore
from forge.security import TokenError, create_state_token, decode_token
from forge.util.http import shared_async_client
from forge.util.ssrf import guarded_request

router = APIRouter(tags=["oauth"])

_PREFIX = "/v1/projects/{project_id}/auth-providers/{ap_id}/oauth"


def _redirect_uri(cfg: dict) -> str:
    return cfg.get("redirect_uri") or f"{settings.public_base_url.rstrip('/')}/v1/oauth/callback"


async def _load(session, tenant_id: str, project_id: str, ap_id: str) -> AuthProvider:
    ap = (
        await session.execute(
            select(AuthProvider).where(
                AuthProvider.tenant_id == tenant_id, AuthProvider.project_id == project_id, AuthProvider.id == ap_id
            )
        )
    ).scalar_one_or_none()
    if ap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "auth provider not found")
    if ap.kind != "oauth2_authorization_code":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "provider is not an oauth2_authorization_code provider")
    return ap


@router.post(_PREFIX + "/start")
async def oauth_start(
    project_id: str, ap_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("editor")),
):
    ap = await _load(session, tenant_id, project_id, ap_id)
    cfg = ap.config or {}
    client_id = await SecretStore().read_ref(tenant_id=tenant_id, project_id=project_id, ref=cfg["client_id_ref"]) if cfg.get("client_id_ref") else None
    if not client_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "client_id secret not configured")
    state = create_state_token({"tid": tenant_id, "pid": project_id, "ap": ap_id})
    q = {
        "response_type": "code",
        "client_id": str(client_id),
        "redirect_uri": _redirect_uri(cfg),
        "state": state,
    }
    if cfg.get("scope"):
        q["scope"] = cfg["scope"]
    return {"authorize_url": f"{cfg['authorize_url']}?{urlencode(q)}"}


@router.get("/v1/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    # Every interpolated value below is provider/redirect-controlled, so HTML-escape it to
    # avoid reflected XSS on the API origin's callback page (audit S9).
    if error:
        return HTMLResponse(f"<h3>Authorization failed</h3><p>{escape(error)}</p>", status_code=400)
    if not code or not state:
        return HTMLResponse("<h3>Missing code/state</h3>", status_code=400)
    try:
        claims = decode_token(state, expected_type="oauth_state")
    except TokenError as e:
        return HTMLResponse(f"<h3>Invalid or expired state</h3><p>{escape(str(e))}</p>", status_code=400)
    tenant_id, project_id, ap_id = claims["tid"], claims["pid"], claims["ap"]
    ap = await _load(session, tenant_id, project_id, ap_id)
    cfg = ap.config or {}

    secrets = SecretStore()
    client_id = await secrets.read_ref(tenant_id=tenant_id, project_id=project_id, ref=cfg["client_id_ref"]) if cfg.get("client_id_ref") else None
    client_secret = await secrets.read_ref(tenant_id=tenant_id, project_id=project_id, ref=cfg["client_secret_ref"]) if cfg.get("client_secret_ref") else None

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(cfg),
        "client_id": str(client_id) if client_id else None,
        "client_secret": str(client_secret) if client_secret else None,
    }
    # Fetch the token through the SSRF guard (validates the host pre-connect AND re-validates
    # any redirect hop, with httpx's cross-origin credential stripping) rather than a raw POST
    # that would follow a redirect to an internal host (audit S8).
    r = await guarded_request(
        shared_async_client(), "POST", cfg["token_url"],
        data={k: v for k, v in data.items() if v is not None}, timeout=30, follow_redirects=True,
    )
    if r.status_code >= 400:
        return HTMLResponse(
            f"<h3>Token exchange failed ({escape(str(r.status_code))})</h3><pre>{escape(r.text[:500])}</pre>",
            status_code=400,
        )
    body = r.json()
    import time as _t

    bundle = {
        "access_token": body.get("access_token"),
        "refresh_token": body.get("refresh_token"),
        "token_type": body.get("token_type", "Bearer"),
        "scope": body.get("scope", cfg.get("scope")),
        "expires_at": (_t.time() + int(body["expires_in"])) if body.get("expires_in") else None,
    }
    await AuthResolver()._store_bundle(tenant_id, project_id, ap_id, bundle)
    return HTMLResponse("<h3>✅ Connected</h3><p>You can close this window and return to Forge.</p>")


@router.get(_PREFIX + "/status")
async def oauth_status(
    project_id: str, ap_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    _: CurrentUser = Depends(require_role("viewer")),
):
    await _load(session, tenant_id, project_id, ap_id)  # validates existence/kind
    try:
        bundle = await SecretStore().read_ref(
            tenant_id=tenant_id, project_id=project_id,
            ref=f"secret://proj/{AuthResolver.bundle_secret_name(ap_id)}",
        )
    except SecretNotFound:
        return {"connected": False}
    return {
        "connected": bool(isinstance(bundle, dict) and bundle.get("access_token")),
        "expires_at": bundle.get("expires_at") if isinstance(bundle, dict) else None,
        "scope": bundle.get("scope") if isinstance(bundle, dict) else None,
        "has_refresh": bool(isinstance(bundle, dict) and bundle.get("refresh_token")),
    }
