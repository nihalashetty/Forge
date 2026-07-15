"""OAuth 2.1 authorization + resource server for Forge's MCP endpoint (MCP authorization spec).

Lets ANY standard MCP client (Claude Desktop, Cursor, VS Code) authenticate a user over
`POST /v1/mcp/{project_id}` without a pre-shared key: the client discovers this server, registers
dynamically (RFC 7591), runs an authorization-code + PKCE flow, and presents the resulting
audience-bound access token as `Authorization: Bearer …`. The MCP router validates the token and
acts AS that user (forge.routers.mcp_server._oauth_end_user).

Standards implemented: OAuth 2.1 (authorization code + PKCE S256, public clients), Protected
Resource Metadata (RFC 9728), Authorization Server Metadata (RFC 8414), Dynamic Client
Registration (RFC 7591), and Resource Indicators (RFC 8707 - the token `aud` is the project's
canonical MCP URL, so tokens can't be replayed at a different resource).

GATED: everything here 404s unless `settings.mcp_oauth_enabled` is on, so an operator opts in after
review. KNOWN REVIEW ITEMS (documented, intentionally conservative): the consent screen is a
minimal server-rendered login form; MFA-enabled accounts are refused here (no MFA bypass) and must
use a personal access token; there is no per-client consent memory. Harden these before relying on
it in production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html as _html
import secrets as _secrets
import urllib.parse

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from forge.config import settings
from forge.db.base import SessionLocal
from forge.models import OAuthClient
from forge.security import (
    TokenError,
    create_mcp_access_token,
    create_mcp_authorization_code,
    create_mcp_refresh_token,
    decode_token,
    revoke,
)
from forge.services.auth import AuthError, AuthService
from forge.util.ratelimit import rate_limiter

router = APIRouter(tags=["mcp-oauth"])


def _enabled() -> None:
    if not settings.mcp_oauth_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP OAuth is not enabled")


def _base() -> str:
    return settings.public_base_url.rstrip("/")


def _valid_redirect(uri: str) -> bool:
    """Only https, or a loopback address for native clients (open-redirect / spec hygiene)."""
    try:
        p = urllib.parse.urlparse(uri)
    except ValueError:
        return False
    if p.scheme == "https":
        return True
    return p.scheme in ("http",) and p.hostname in ("localhost", "127.0.0.1", "::1")


def _pkce_ok(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256((verifier or "").encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(computed, challenge or "")


async def _load_client(client_id: str | None) -> OAuthClient | None:
    if not client_id:
        return None
    async with SessionLocal() as s:
        return (await s.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))).scalar_one_or_none()


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status_code)


# --- Discovery (RFC 8414 / RFC 9728) --------------------------------------------------------
@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata():
    _enabled()
    b = _base()
    return {
        "issuer": b,
        "authorization_endpoint": f"{b}/v1/oauth/authorize",
        "token_endpoint": f"{b}/v1/oauth/token",
        "registration_endpoint": f"{b}/v1/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


@router.get("/.well-known/oauth-protected-resource/v1/mcp/{project_id}")
async def protected_resource_metadata(project_id: str):
    _enabled()
    b = _base()
    return {"resource": f"{b}/v1/mcp/{project_id}", "authorization_servers": [b]}


# --- Dynamic client registration (RFC 7591) -------------------------------------------------
@router.post("/v1/oauth/register", status_code=201)
async def register_client(body: dict):
    _enabled()
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _oauth_error("invalid_client_metadata", "redirect_uris is required")
    if not all(isinstance(u, str) and _valid_redirect(u) for u in redirect_uris):
        return _oauth_error("invalid_redirect_uri", "redirect_uris must be https or loopback http")
    client_id = "mcp_" + _secrets.token_urlsafe(24)
    name = str(body.get("client_name"))[:200] if body.get("client_name") else None
    async with SessionLocal() as s:
        s.add(OAuthClient(client_id=client_id, client_name=name, redirect_uris=redirect_uris))
        await s.commit()
    return {
        "client_id": client_id,
        "client_name": name,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }


# --- Authorization endpoint (code + PKCE) ---------------------------------------------------
# Branded consent page - mirrors the console's login card (Forge wordmark, accent-orange
# button, card on a tinted background) so the OAuth login reads as Forge, while staying a
# self-contained server-rendered page (the authorization server can't depend on the SPA).
# Uses %%…%% sentinels (not str.format/f-string) so the CSS braces need no escaping. Every
# injected value is HTML-escaped; HIDDEN/ERR are substituted before NAME so a sentinel-looking
# client name can't be re-substituted (str.replace is single-pass, non-recursive).
_CONSENT_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Authorize %%NAME%% · Forge</title>
<style>
  :root{--bg:#F6F7F9;--card:#FFFFFF;--line:#E2E6EC;--fg:#11161C;--fg2:#7A848F;--accent:#E8541F;--accent-dim:#B8420F;--err:#D23A34;}
  @media (prefers-color-scheme:dark){:root{--bg:#0A0C0F;--card:#0F1318;--line:#2A323D;--fg:#EEF2F6;--fg2:#7A848F;--accent:#FF6A3D;--accent-dim:#C24E2B;--err:#F2615B;}}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;}
  .card{width:380px;max-width:calc(100vw - 32px);background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px;box-shadow:0 12px 40px rgba(17,22,28,.12);}
  .brand{font-size:22px;font-weight:700;letter-spacing:-.01em;margin:0 0 4px;}
  .sub{color:var(--fg2);font-size:14px;line-height:1.5;margin:0 0 20px;}
  .sub b{color:var(--fg);font-weight:600;}
  label{display:block;margin-bottom:12px;}
  .lbl{display:block;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--fg2);margin-bottom:6px;}
  .lbl .hint{text-transform:none;letter-spacing:0;font-weight:400;}
  input{width:100%;height:38px;padding:0 12px;font-size:14px;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px;outline:none;}
  input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(232,84,31,.15);}
  button{width:100%;height:40px;margin-top:6px;border:0;border-radius:8px;background:var(--accent);color:#fff;font-size:14px;font-weight:600;cursor:pointer;}
  button:hover{background:var(--accent-dim);}
  .err{color:var(--err);font-size:13px;margin-bottom:12px;}
  .fieldhint{color:var(--fg2);font-size:12px;line-height:1.45;margin:-6px 0 14px;}
</style></head>
<body>
<form class="card" method="post" action="/v1/oauth/authorize">
  <div class="brand">Forge</div>
  <div class="sub"><b>%%NAME%%</b> wants to access your Forge tools over MCP, acting as you.</div>
  %%ERR%%
  %%HIDDEN%%
  <label><span class="lbl">Email</span><input name="email" type="email" required autocomplete="username"></label>
  <label><span class="lbl">Password</span><input name="password" type="password" required autocomplete="current-password"></label>
  <label><span class="lbl">Workspace id <span class="hint">(optional)</span></span><input name="workspace_id"></label>
  <div class="fieldhint">Leave blank unless the same email is registered in multiple workspaces. It's your workspace ID (Settings &gt; General), not the project ID.</div>
  <button type="submit">Authorize</button>
</form>
</body></html>"""


def _consent_html(fields: dict, client: OAuthClient, error: str | None = None) -> str:
    def h(v: object) -> str:
        return _html.escape(str(v or ""))

    hidden = "".join(
        f'<input type="hidden" name="{h(k)}" value="{h(v)}">'
        for k, v in fields.items()
    )
    err = f'<div class="err">{h(error)}</div>' if error else ""
    name = h(client.client_name or client.client_id)
    return (
        _CONSENT_TEMPLATE
        .replace("%%HIDDEN%%", hidden)
        .replace("%%ERR%%", err)
        .replace("%%NAME%%", name)
    )


def _authorize_fields(q: dict) -> dict:
    return {
        "client_id": q.get("client_id", ""),
        "redirect_uri": q.get("redirect_uri", ""),
        "code_challenge": q.get("code_challenge", ""),
        "state": q.get("state", ""),
        "resource": q.get("resource", ""),
        "scope": q.get("scope", ""),
    }


@router.get("/v1/oauth/authorize", response_class=HTMLResponse)
async def authorize_form(request: Request):
    _enabled()
    q = dict(request.query_params)
    if q.get("response_type") != "code":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "response_type must be 'code'")
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "PKCE with code_challenge_method=S256 is required")
    client = await _load_client(q.get("client_id"))
    if client is None or q.get("redirect_uri") not in (client.redirect_uris or []):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown client_id or unregistered redirect_uri")
    return HTMLResponse(_consent_html(_authorize_fields(q), client))


@router.post("/v1/oauth/authorize")
async def authorize_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    workspace_id: str = Form(""),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    state: str = Form(""),
    resource: str = Form(""),
    scope: str = Form(""),
):
    _enabled()
    fields = {"client_id": client_id, "redirect_uri": redirect_uri, "code_challenge": code_challenge,
              "state": state, "resource": resource, "scope": scope}
    client = await _load_client(client_id)
    if client is None or redirect_uri not in (client.redirect_uris or []):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown client_id or unregistered redirect_uri")
    # Throttle the credential form per client IP (brute-force guard on this login surface).
    ip = request.client.host if request.client else "?"
    if not rate_limiter.allow(f"oauth_authorize:{ip}", rate=20, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts, slow down")

    async with SessionLocal() as s:
        try:
            user = await AuthService.authenticate(s, email=email, password=password, tenant_id=workspace_id or None)
        except AuthError as e:
            return HTMLResponse(_consent_html(fields, client, error=str(e)), status_code=401)
        # No MFA bypass: an account with TOTP enabled must not authorize via this minimal form.
        if await AuthService.totp_status(s, user.id):
            return HTMLResponse(
                _consent_html(fields, client, error="This account uses MFA; use a personal access token for MCP instead."),
                status_code=401,
            )

    code = create_mcp_authorization_code(claims={
        "sub": user.id, "tid": user.tenant_id, "role": user.role,
        "cid": client_id, "ru": redirect_uri, "cc": code_challenge, "res": resource or "",
    })
    sep = "&" if "?" in redirect_uri else "?"
    url = f"{redirect_uri}{sep}code={urllib.parse.quote(code)}"
    if state:
        url += f"&state={urllib.parse.quote(state)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


# --- Token endpoint -------------------------------------------------------------------------
@router.post("/v1/oauth/token")
async def token(
    grant_type: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
):
    _enabled()
    if grant_type == "authorization_code":
        try:
            claims = decode_token(code, expected_type="mcp_auth_code")
        except TokenError:
            return _oauth_error("invalid_grant", "invalid or expired authorization code")
        if claims.get("cid") != client_id or claims.get("ru") != redirect_uri:
            return _oauth_error("invalid_grant", "client_id / redirect_uri mismatch")
        if not _pkce_ok(code_verifier, claims.get("cc", "")):
            return _oauth_error("invalid_grant", "PKCE verification failed")
        # Single-use: burn the code's jti so a replayed authorization code is rejected (decode_token
        # checks the revocation denylist). `exp` lets the entry self-prune.
        revoke(claims.get("jti"), exp=claims.get("exp"))
        resource = claims.get("res") or ""
        access = create_mcp_access_token(claims={
            "sub": claims["sub"], "tid": claims["tid"], "role": claims.get("role"),
            "res": resource, "cid": client_id,
        })
        refresh = create_mcp_refresh_token(claims={
            "sub": claims["sub"], "tid": claims["tid"], "role": claims.get("role"),
            "cid": client_id, "res": resource,
        })
        return {"access_token": access, "token_type": "Bearer", "expires_in": 3600, "refresh_token": refresh, "scope": ""}

    if grant_type == "refresh_token":
        try:
            claims = decode_token(refresh_token, expected_type="mcp_refresh")
        except TokenError:
            return _oauth_error("invalid_grant", "invalid refresh_token")
        if client_id and claims.get("cid") != client_id:
            return _oauth_error("invalid_grant", "client mismatch")
        resource = claims.get("res") or ""
        access = create_mcp_access_token(claims={
            "sub": claims["sub"], "tid": claims["tid"], "role": claims.get("role"),
            "res": resource, "cid": claims.get("cid"),
        })
        return {"access_token": access, "token_type": "Bearer", "expires_in": 3600, "scope": ""}

    return _oauth_error("unsupported_grant_type", f"unsupported grant_type {grant_type!r}")
