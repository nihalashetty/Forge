"""Per-user bearer/api_key auth: a provider marked per-user (per_user_context_keys) injects EACH
end user's OWN self-served token, with no shared secret and no passthrough of the inbound token.
Companion to test_connected_credentials.py (which covers the oauth2_authorization_code path)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from forge.auth_providers.resolver import AuthResolver
from forge.db.base import SessionLocal
from forge.main import create_app
from forge.services.auth_providers import AuthProviderService


async def _provider(tenant: str, project: str, kind: str, extra: dict) -> str:
    async with SessionLocal() as s:
        ap = await AuthProviderService.create(
            s, tenant, project, name="portal", kind=kind,
            config={"per_user_context_keys": ["end_user_id"], **extra},
        )
        return ap.id


async def test_per_user_bearer_resolves_each_users_own_token():
    tenant, project = "t_pu_b", "p_pu_b"
    ap_id = await _provider(tenant, project, "bearer", {"header_name": "Authorization", "prefix": "Bearer "})
    async with SessionLocal() as s:
        ap = await AuthProviderService.get(s, tenant, ap_id)
        await AuthProviderService.set_user_connection(s, tenant, project, ap, "user-A", bundle={"access_token": "PAT-A"})
        await AuthProviderService.set_user_connection(s, tenant, project, ap, "user-B", bundle={"access_token": "PAT-B"})

    resolver = AuthResolver()
    ra = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "user-A"})
    assert ra.headers["Authorization"] == "Bearer PAT-A"
    rb = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "user-B"})
    assert rb.headers["Authorization"] == "Bearer PAT-B"

    # A user who hasn't connected their own token yet gets a clear "not connected" error, never
    # a silent miss or another user's token.
    with pytest.raises(KeyError):
        await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "user-C"})


async def test_per_user_api_key_resolves_each_users_own_value():
    tenant, project = "t_pu_k", "p_pu_k"
    ap_id = await _provider(tenant, project, "api_key", {"in": "header", "name": "X-API-Key"})
    async with SessionLocal() as s:
        ap = await AuthProviderService.get(s, tenant, ap_id)
        await AuthProviderService.set_user_connection(s, tenant, project, ap, "u1", bundle={"access_token": "KEY-1"})

    resolver = AuthResolver()
    ra = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "u1"})
    assert ra.headers["X-API-Key"] == "KEY-1"
    with pytest.raises(KeyError):
        await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "u2"})


async def test_per_user_bearer_inline_token_wins_and_no_cache_collision():
    """A per-user provider with token_ctx_key forwards an INLINE token from the run context (the
    web-chat /run path) ahead of the stored connection, and different inline tokens sharing the same
    end_user dims must not collide in the resolver cache."""
    tenant, project = "t_pu_i", "p_pu_i"
    ap_id = await _provider(tenant, project, "bearer",
                            {"header_name": "Authorization", "prefix": "Bearer ", "token_ctx_key": "quoting_pat"})
    resolver = AuthResolver()
    ra = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id,
                                context={"end_user_id": "anon", "quoting_pat": "INLINE-A"})
    assert ra.headers["Authorization"] == "Bearer INLINE-A"
    # Different inline token, same end_user dims -> must NOT be served the cached "INLINE-A".
    rb = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id,
                                context={"end_user_id": "anon", "quoting_pat": "INLINE-B"})
    assert rb.headers["Authorization"] == "Bearer INLINE-B"
    # No inline token -> falls back to the stored per-user connection (MCP/console path).
    async with SessionLocal() as s:
        ap = await AuthProviderService.get(s, tenant, ap_id)
        await AuthProviderService.set_user_connection(s, tenant, project, ap, "stored-user", bundle={"access_token": "STORED"})
    rc = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id,
                                context={"end_user_id": "stored-user"})
    assert rc.headers["Authorization"] == "Bearer STORED"


async def test_extra_headers_literal_and_secret_ref():
    """A provider's extra_headers are stamped on every call: literals verbatim, secret:// refs
    resolved from the secret store — so a shared service token lives in the store, never hardcoded."""
    from forge.secrets.store import SecretStore
    tenant, project = "t_eh", "p_eh"
    async with SessionLocal() as s:
        await SecretStore().write(s, tenant_id=tenant, project_id=project, name="primary", value="PRIMARY")
        await SecretStore().write(s, tenant_id=tenant, project_id=project, name="svc_tok", value="SVC-123")
        ap = await AuthProviderService.create(
            s, tenant, project, name="q", kind="bearer",
            config={"kind": "bearer", "token_ref": "secret://proj/primary", "header_name": "Authorization",
                    "prefix": "Bearer ", "extra_headers": {"X-Forge-Client-Id": "forge",
                                                           "X-Forge-Service-Token": "secret://proj/svc_tok"}},
        )
        ap_id = ap.id

    r = await AuthResolver().resolve(tenant_id=tenant, project_id=project, provider_id=ap_id)
    assert r.headers["Authorization"] == "Bearer PRIMARY"
    assert r.headers["X-Forge-Client-Id"] == "forge"           # literal, verbatim
    assert r.headers["X-Forge-Service-Token"] == "SVC-123"     # resolved from the secret store


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")


async def test_connections_router_self_service_end_to_end():
    """The connector-safe /connections router is served (not the /auth-providers admin surface) and
    a logged-in user can discover per-user providers + set/read/clear THEIR OWN token. Role is not
    gated here, so a connector uses the identical path; resolver injection keyed by end_user_id is
    covered by the unit tests above."""
    async with _http() as c:
        reg = (await c.post("/v1/auth/register", json={"email": f"pu{uuid.uuid4().hex[:8]}@ex.com", "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        pid = (await c.post("/v1/projects", json={"name": "PU"}, headers=h)).json()["id"]

        # Owner creates a PER-USER bearer provider (no shared secret; each user brings their own).
        ap = (await c.post(f"/v1/projects/{pid}/auth-providers", json={
            "name": "portal", "kind": "bearer",
            "config": {"kind": "bearer", "header_name": "Authorization", "prefix": "Bearer ",
                       "per_user_context_keys": ["end_user_id"]},
        }, headers=h)).json()
        ap_id = ap["id"]

        # Discovery lists it as not-yet-connected for the caller.
        lst = (await c.get(f"/v1/projects/{pid}/connections", headers=h)).json()
        assert [x for x in lst if x["id"] == ap_id and x["connected"] is False], lst

        # Set my own token -> 204, reads back connected.
        r = await c.put(f"/v1/projects/{pid}/connections/{ap_id}", json={"access_token": "MY-TOKEN"}, headers=h)
        assert r.status_code == 204, r.text
        assert (await c.get(f"/v1/projects/{pid}/connections/{ap_id}", headers=h)).json()["connected"] is True
        lst2 = (await c.get(f"/v1/projects/{pid}/connections", headers=h)).json()
        assert [x for x in lst2 if x["id"] == ap_id and x["connected"] is True]

        # The provider's own /test now resolves the CALLER's connected token: console tests run AS
        # the current user, so a per-user provider no longer reports "not connected" for the tester.
        t = (await c.post(f"/v1/projects/{pid}/auth-providers/{ap_id}/test", json={}, headers=h)).json()
        assert t.get("ok") is True, t
        assert "Authorization" in (t.get("headers") or {}), t

        # Clear -> not connected again.
        assert (await c.delete(f"/v1/projects/{pid}/connections/{ap_id}", headers=h)).status_code == 204
        assert (await c.get(f"/v1/projects/{pid}/connections/{ap_id}", headers=h)).json()["connected"] is False


async def test_connections_rejects_non_per_user_provider():
    """A shared (non-per-user) provider can't take a per-user token via /connections."""
    async with _http() as c:
        reg = (await c.post("/v1/auth/register", json={"email": f"pu{uuid.uuid4().hex[:8]}@ex.com", "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        pid = (await c.post("/v1/projects", json={"name": "PU2"}, headers=h)).json()["id"]
        ap = (await c.post(f"/v1/projects/{pid}/auth-providers", json={
            "name": "shared", "kind": "bearer",
            "config": {"kind": "bearer", "token_ref": "secret://proj/token"},
        }, headers=h)).json()
        r = await c.put(f"/v1/projects/{pid}/connections/{ap['id']}", json={"access_token": "X"}, headers=h)
        assert r.status_code == 400, r.text
        # And it never appears in the connector's per-user discovery list.
        assert all(x["id"] != ap["id"] for x in (await c.get(f"/v1/projects/{pid}/connections", headers=h)).json())
