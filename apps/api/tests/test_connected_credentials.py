"""Per-user connected credentials: the AuthResolver picks each end user's own stored OAuth bundle,
so a tool acts as the authenticated user downstream without the MCP token being passed through."""

from __future__ import annotations

import time

import pytest

from forge.auth_providers.resolver import AuthResolver
from forge.db.base import SessionLocal
from forge.services.auth_providers import AuthProviderService


async def _provider(tenant: str, project: str) -> str:
    async with SessionLocal() as s:
        ap = await AuthProviderService.create(
            s, tenant, project, name="portal", kind="oauth2_authorization_code",
            config={
                "per_user_context_keys": ["end_user_id"],
                "token_url": "https://example.com/token",
                "header_name": "Authorization",
                "prefix": "Bearer ",
            },
        )
        return ap.id


async def test_per_user_connected_credentials_resolve_per_end_user():
    tenant, project = "t_conn", "p_conn"
    ap_id = await _provider(tenant, project)
    async with SessionLocal() as s:
        ap = await AuthProviderService.get(s, tenant, ap_id)
        await AuthProviderService.set_user_connection(
            s, tenant, project, ap, "user-A", bundle={"access_token": "tok-A", "expires_at": time.time() + 3600})
        await AuthProviderService.set_user_connection(
            s, tenant, project, ap, "user-B", bundle={"access_token": "tok-B", "expires_at": time.time() + 3600})

    resolver = AuthResolver()
    ra = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "user-A"})
    assert ra.headers["Authorization"] == "Bearer tok-A"
    rb = await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "user-B"})
    assert rb.headers["Authorization"] == "Bearer tok-B"

    # a user who never connected their account cannot authenticate (no bundle -> "not connected")
    with pytest.raises(KeyError):
        await resolver.resolve(tenant_id=tenant, project_id=project, provider_id=ap_id, context={"end_user_id": "user-C"})


async def test_connection_status_and_clear():
    tenant, project = "t_conn2", "p_conn2"
    ap_id = await _provider(tenant, project)
    async with SessionLocal() as s:
        ap = await AuthProviderService.get(s, tenant, ap_id)
        assert (await AuthProviderService.get_user_connection(tenant, project, ap, "u1"))["connected"] is False
        await AuthProviderService.set_user_connection(s, tenant, project, ap, "u1", bundle={"access_token": "t", "expires_at": time.time() + 3600})
    async with SessionLocal() as s:
        ap = await AuthProviderService.get(s, tenant, ap_id)
        assert (await AuthProviderService.get_user_connection(tenant, project, ap, "u1"))["connected"] is True
        await AuthProviderService.clear_user_connection(s, tenant, project, ap, "u1")
    async with SessionLocal() as s:
        ap = await AuthProviderService.get(s, tenant, ap_id)
        assert (await AuthProviderService.get_user_connection(tenant, project, ap, "u1"))["connected"] is False
