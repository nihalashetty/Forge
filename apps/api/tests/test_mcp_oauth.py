"""OAuth 2.1 for MCP: discovery, dynamic client registration, authorization-code + PKCE, single-use
codes, audience binding, and token validation on the MCP endpoint. Gated by mcp_oauth_enabled."""

from __future__ import annotations

import base64
import hashlib
import os
import urllib.parse
import uuid

import httpx

from forge.config import settings
from forge.main import create_app


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _authorize_code(c, *, email, password, client_id, redirect_uri, challenge, resource) -> str:
    form = {
        "email": email, "password": password, "workspace_id": "", "client_id": client_id,
        "redirect_uri": redirect_uri, "code_challenge": challenge, "state": "s", "resource": resource, "scope": "",
    }
    sub = await c.post("/v1/oauth/authorize", data=form, follow_redirects=False)
    assert sub.status_code == 302, sub.text
    q = urllib.parse.parse_qs(urllib.parse.urlparse(sub.headers["location"]).query)
    assert q.get("state") == ["s"]
    return q["code"][0]


async def test_mcp_oauth_disabled_returns_404():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/.well-known/oauth-authorization-server")).status_code == 404


async def test_mcp_oauth_end_to_end(monkeypatch):
    monkeypatch.setattr(settings, "mcp_oauth_enabled", True)
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # a real user + a project (in the user's tenant) with one tool
        email = f"o{uuid.uuid4().hex[:10]}@example.com"
        reg = await c.post("/v1/auth/register", json={"email": email, "password": "supersecret1"})
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        pid = (await c.post("/v1/projects", json={"name": "OAuth", "slug": "oauth-proj"})).json()["id"]
        tid = (await c.post(f"/v1/projects/{pid}/tools", json={"name": "calc", "kind": "builtin", "config": {"builtin": "calculator", "description": "c"}})).json()["id"]
        # publish the tool via an exposed tool set (the MCP surface = exposed sets' enabled tools)
        await c.post(f"/v1/projects/{pid}/tool-sets", json={"name": "General", "tool_ids": [tid]})

        # discovery is live when enabled
        asm = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert asm["code_challenge_methods_supported"] == ["S256"]
        prm = (await c.get(f"/.well-known/oauth-protected-resource/v1/mcp/{pid}")).json()
        assert prm["resource"].endswith(f"/v1/mcp/{pid}") and prm["authorization_servers"]

        # dynamic client registration (RFC 7591)
        redirect_uri = "http://localhost/callback"
        rc = await c.post("/v1/oauth/register", json={"redirect_uris": [redirect_uri], "client_name": "Test client"})
        assert rc.status_code == 201, rc.text
        client_id = rc.json()["client_id"]

        # the consent form renders
        verifier, challenge = _pkce()
        resource = f"{settings.public_base_url.rstrip('/')}/v1/mcp/{pid}"
        gf = await c.get("/v1/oauth/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource, "state": "s",
        })
        assert gf.status_code == 200 and "Authorize" in gf.text

        # authorization-code exchange (PKCE verifier) -> access token
        code = await _authorize_code(c, email=email, password="supersecret1", client_id=client_id,
                                     redirect_uri=redirect_uri, challenge=challenge, resource=resource)
        tok = await c.post("/v1/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier,
        })
        assert tok.status_code == 200, tok.text
        access = tok.json()["access_token"]
        assert tok.json()["token_type"] == "Bearer"

        # the audience-bound token authorizes the MCP endpoint
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        oauth_headers = {"Authorization": f"Bearer {access}"}
        r = await c.post(f"/v1/mcp/{pid}", headers=oauth_headers, json=body)
        assert r.status_code == 200 and any(t["name"] == "calc" for t in r.json()["result"]["tools"])

        # no credential -> 401 with an RFC 9728 discovery pointer
        no = await c.post(f"/v1/mcp/{pid}", json=body)
        assert no.status_code == 401 and "resource_metadata" in no.headers.get("www-authenticate", "")

        # audience binding: the token must not work on a different project
        pid2 = (await c.post("/v1/projects", json={"name": "Other", "slug": "oauth-other"})).json()["id"]
        assert (await c.post(f"/v1/mcp/{pid2}", headers=oauth_headers, json=body)).status_code == 401

        # single-use: replaying the same authorization code is rejected
        replay = await c.post("/v1/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier,
        })
        assert replay.status_code == 400

        # PKCE is enforced: a fresh code with the wrong verifier fails
        v2, ch2 = _pkce()
        code2 = await _authorize_code(c, email=email, password="supersecret1", client_id=client_id,
                                      redirect_uri=redirect_uri, challenge=ch2, resource=resource)
        bad = await c.post("/v1/oauth/token", data={
            "grant_type": "authorization_code", "code": code2, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier,  # wrong verifier
        })
        assert bad.status_code == 400
