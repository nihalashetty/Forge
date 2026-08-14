"""Forge as an OAuth *client* of a remote MCP server (the MCP authorization spec, client half).

`routers/mcp_oauth.py` already implements the SERVER half - Forge issuing tokens so Claude
Desktop / Cursor can call Forge's MCP endpoint. This module is its mirror image: Forge
discovering, registering with, and holding tokens for SOMEBODY ELSE'S MCP server, which is what
`mcp.slack.com`, the Google Workspace and Microsoft servers, Notion, Linear and Atlassian all
require.

Flow, all of it SSRF-guarded:

  1. GET  <server>/.well-known/oauth-protected-resource   (RFC 9728) -> authorization_servers[]
  2. GET  <as>/.well-known/oauth-authorization-server     (RFC 8414) -> authorize/token/register
  3. POST <register>                                      (RFC 7591) -> client_id [+ secret]
  4. authorization-code + PKCE via the existing /v1/oauth/callback

Steps 1-3 are best-effort: a manifest that declares static `authorize_url`/`token_url` skips
discovery entirely, so a server with no metadata (or a locked-down network) still connects. That
fallback is why `auth.discover` is opt-in per manifest rather than always-on.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse

from forge.config import settings
from forge.util.http import shared_async_client
from forge.util.ssrf import guarded_request

log = logging.getLogger("forge.connectors.mcp_auth")

# Discovery must not stall a connect click. These endpoints are small JSON documents on the
# vendor's own edge; a server that can't answer in this window is one we fall back for.
_DISCOVERY_TIMEOUT = 10.0


class DiscoveryError(RuntimeError):
    pass


async def _get_json(url: str, *, client=None) -> dict[str, Any] | None:
    try:
        r = await guarded_request(
            client or shared_async_client(), "GET", url,
            timeout=_DISCOVERY_TIMEOUT, follow_redirects=True,
            headers={"Accept": "application/json", "MCP-Protocol-Version": "2026-07-28"},
        )
    except Exception as e:  # noqa: BLE001 - unreachable/blocked/timeout are all "no metadata"
        log.debug("oauth discovery: %s failed (%s)", url, e)
        return None
    if r.status_code >= 400:
        return None
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON 200 is not metadata
        return None
    return body if isinstance(body, dict) else None


def _origin(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


async def discover(mcp_url: str, *, client=None) -> dict[str, Any]:
    """Resolve a remote MCP server URL to its OAuth endpoints.

    Returns {authorize_url, token_url, registration_url, scopes_supported, issuer} with any
    key absent when the server didn't advertise it. Never raises for a server that simply has
    no metadata - the caller falls back to the manifest's static endpoints.
    """
    out: dict[str, Any] = {}
    origin = _origin(mcp_url)
    path = urlparse(mcp_url).path.rstrip("/")

    # RFC 9728 permits both a path-scoped and a root-scoped resource document. Try the
    # path-scoped one first: a host serving several MCP resources distinguishes them that way.
    prm = None
    for candidate in (
        f"{origin}/.well-known/oauth-protected-resource{path}" if path else None,
        f"{origin}/.well-known/oauth-protected-resource",
    ):
        if not candidate:
            continue
        prm = await _get_json(candidate, client=client)
        if prm:
            break

    as_urls: list[str] = []
    if prm:
        servers = prm.get("authorization_servers") or []
        as_urls = [s for s in servers if isinstance(s, str)]
        if prm.get("scopes_supported"):
            out["scopes_supported"] = prm["scopes_supported"]
    if not as_urls:
        # No protected-resource document: the MCP server's own origin is the conventional
        # fallback issuer, which is what most single-tenant servers implement.
        as_urls = [origin]

    for as_url in as_urls:
        as_origin = _origin(as_url)
        as_path = urlparse(as_url).path.rstrip("/")
        for candidate in (
            f"{as_origin}/.well-known/oauth-authorization-server{as_path}" if as_path else None,
            f"{as_origin}/.well-known/oauth-authorization-server",
            urljoin(as_origin + "/", ".well-known/openid-configuration"),
        ):
            if not candidate:
                continue
            meta = await _get_json(candidate, client=client)
            if not meta:
                continue
            if meta.get("authorization_endpoint"):
                out["authorize_url"] = meta["authorization_endpoint"]
            if meta.get("token_endpoint"):
                out["token_url"] = meta["token_endpoint"]
            if meta.get("registration_endpoint"):
                out["registration_url"] = meta["registration_endpoint"]
            if meta.get("issuer"):
                out["issuer"] = meta["issuer"]
            if meta.get("scopes_supported") and "scopes_supported" not in out:
                out["scopes_supported"] = meta["scopes_supported"]
            if out.get("authorize_url") and out.get("token_url"):
                return out
    return out


async def register_client(
    registration_url: str, *, client_name: str, redirect_uri: str, scopes: list[str] | None = None, client=None
) -> dict[str, Any]:
    """Dynamic Client Registration (RFC 7591) against the remote authorization server.

    Requests a CONFIDENTIAL client (`client_secret_post`) because Forge holds the secret
    server-side in its own encrypted store - a public client would be the wrong choice here and
    would force PKCE to carry the whole burden. Servers that only issue public clients simply
    return no `client_secret`, which the token exchange handles.
    """
    body = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }
    if scopes:
        body["scope"] = " ".join(scopes)
    r = await guarded_request(
        client or shared_async_client(), "POST", registration_url,
        json=body, timeout=_DISCOVERY_TIMEOUT, follow_redirects=True,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if r.status_code >= 400:
        raise DiscoveryError(f"client registration failed ({r.status_code}): {r.text[:300]}")
    data = r.json()
    if not data.get("client_id"):
        raise DiscoveryError("client registration returned no client_id")
    return data


def callback_url() -> str:
    """The single redirect URI Forge registers everywhere - the same endpoint the auth-provider
    OAuth connect flow already uses, so an operator only ever whitelists one URL per install."""
    return f"{settings.public_base_url.rstrip('/')}/v1/oauth/callback"
