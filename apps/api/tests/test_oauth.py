"""3-legged OAuth (authorization_code) resolver + refresh + state-token tests."""

from __future__ import annotations

import time

import httpx

from forge.auth_providers.resolver import AuthResolver
from forge.db.base import SessionLocal
from forge.models import AuthProvider
from forge.secrets.store import SecretStore
from forge.security import create_state_token, decode_token

_CFG = {
    "kind": "oauth2_authorization_code",
    "authorize_url": "https://idp.example/authorize",
    "token_url": "https://idp.example/token",
    "client_id_ref": "secret://proj/cid",
    "client_secret_ref": "secret://proj/csec",
}


async def _store_bundle(tenant, project, ap_id, bundle):
    async with SessionLocal() as s:
        await SecretStore().write(s, tenant_id=tenant, project_id=project, name=f"oauth_token__{ap_id}", value=bundle, kind="oauth")


def _ap(ap_id="ap_oauth", tenant="t_o", project="p_o"):
    return AuthProvider(id=ap_id, tenant_id=tenant, project_id=project, name="idp", kind="oauth2_authorization_code", config=_CFG)


async def test_oauth_resolves_valid_bundle():
    await _store_bundle("t_o", "p_o", "ap_oauth", {"access_token": "TKN", "expires_at": time.time() + 3600})
    resolved = await AuthResolver().resolve(tenant_id="t_o", project_id="p_o", provider_id="ap_oauth", provider=_ap(), force=True)
    assert resolved.headers["Authorization"] == "Bearer TKN"


async def test_oauth_refreshes_expired_token():
    async with SessionLocal() as s:
        await SecretStore().write(s, tenant_id="t_o2", project_id="p_o2", name="cid", value="client-id")
        await SecretStore().write(s, tenant_id="t_o2", project_id="p_o2", name="csec", value="client-secret")
    await _store_bundle("t_o2", "p_o2", "ap2", {"access_token": "OLD", "refresh_token": "R1", "expires_at": time.time() - 10})

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"access_token": "NEW", "expires_in": 3600})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resolved = await AuthResolver().resolve(tenant_id="t_o2", project_id="p_o2", provider_id="ap2",
                                            provider=_ap("ap2", "t_o2", "p_o2"), client=client, force=True)
    await client.aclose()
    assert resolved.headers["Authorization"] == "Bearer NEW"
    assert "grant_type=refresh_token" in seen["body"]
    # the refreshed bundle was persisted
    new_bundle = await SecretStore().read_ref(tenant_id="t_o2", project_id="p_o2", ref="secret://proj/oauth_token__ap2")
    assert new_bundle["access_token"] == "NEW"


async def test_bearer_tolerates_stale_credentials_ref():
    """Regression: a bearer provider whose own token_ref resolves must succeed even when the
    legacy `credentials_ref` fallback points at a secret that no longer exists (the create flow
    used to copy the template default there, then it rotted when the token_ref was edited)."""
    async with SessionLocal() as s:
        await SecretStore().write(s, tenant_id="t_b", project_id="p_b", name="git_token", value="ghp_real")
    ap = AuthProvider(
        id="ap_bearer", tenant_id="t_b", project_id="p_b", name="git", kind="bearer",
        config={"kind": "bearer", "token_ref": "secret://proj/git_token", "header_name": "Authorization", "prefix": "Bearer "},
        credentials_ref="secret://proj/token",  # stale - no secret named "token" exists
    )
    resolved = await AuthResolver().resolve(tenant_id="t_b", project_id="p_b", provider_id="ap_bearer", provider=ap, force=True)
    assert resolved.headers["Authorization"] == "Bearer ghp_real"


async def test_secret_usage_finds_references():
    """The pre-delete guard must surface entities that reference a secret (here, an auth provider)."""
    from forge.services.secrets import SecretService
    async with SessionLocal() as s:
        s.add(AuthProvider(
            id="ap_use", tenant_id="t_u", project_id="p_u", name="shared", kind="bearer",
            config={"kind": "bearer", "token_ref": "secret://proj/shared_key"},
        ))
        await s.commit()
        refs = await SecretService.usage(s, "t_u", "p_u", name="shared_key")
        none = await SecretService.usage(s, "t_u", "p_u", name="unreferenced")
    assert any(r["type"] == "auth_provider" and r["label"] == "shared" for r in refs)
    assert none == []


async def test_oauth_state_token_roundtrip():
    tok = create_state_token({"tid": "t", "pid": "p", "ap": "x"})
    claims = decode_token(tok, expected_type="oauth_state")
    assert claims["tid"] == "t" and claims["ap"] == "x"


async def test_oauth_not_connected_raises():
    import pytest
    with pytest.raises((KeyError, Exception)):
        await AuthResolver().resolve(tenant_id="t_none", project_id="p_none", provider_id="apx", provider=_ap("apx", "t_none", "p_none"), force=True)


class _FakeStore:
    async def read_ref(self, **kw):
        return "cid-123"


async def _authorize_query(cfg: dict) -> dict[str, list[str]]:
    from urllib.parse import parse_qs, urlparse

    from forge.auth_providers.oauth_flow import build_authorize_url

    ap = AuthProvider(id="ap_q", tenant_id="t", project_id="p", name="idp",
                      kind="oauth2_authorization_code", config=cfg)
    url = await build_authorize_url(ap, tenant_id="t", project_id="p", secrets=_FakeStore())
    return parse_qs(urlparse(url).query)


async def test_authorize_params_is_the_only_way_to_add_authorize_query_params():
    """There is ONE mechanism for vendor-specific authorize parameters.

    There used to be two: a generic `authorize_params` dict, and a top-level special case reading
    `cfg["access_type"]` / `cfg["prompt"]`. Nothing ever wrote the top-level spelling, so that
    branch was dead on every path a connector takes - while looking authoritative enough that
    four Google manifests shipped without asking for offline access. A second spelling that
    works in some places and not others is how that recurs, so the top-level one is gone and
    this pins it.
    """
    q = await _authorize_query({**_CFG, "access_type": "offline", "prompt": "consent"})
    assert "access_type" not in q, (
        "top-level access_type must not be a second, undocumented spelling - use authorize_params"
    )
    assert "prompt" not in q

    q = await _authorize_query({**_CFG, "authorize_params": {"access_type": "offline", "user_scope": "chat:write"}})
    assert q["access_type"] == ["offline"] and q["user_scope"] == ["chat:write"]


async def test_authorize_params_cannot_trample_the_protocol_parameters():
    """The extras are vendor data, applied last - they must never be able to redirect the
    callback somewhere else or drop PKCE down to plain."""
    q = await _authorize_query({
        **_CFG,
        "authorize_params": {
            "redirect_uri": "https://attacker.example/steal",
            "code_challenge_method": "plain",
            "state": "forged",
        },
    })
    assert q["redirect_uri"] != ["https://attacker.example/steal"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] != ["forged"]


async def test_per_user_connect_bundle_is_resolvable():
    """Item 5: the connect callback now stores the bundle under the SAME per-user secret name
    that resolve/refresh read. Before the fix it wrote the default name, so a per-user provider's
    token was invisible to resolve. This exercises the connect-time name -> resolve round trip."""
    import pytest

    cfg = {**_CFG, "per_user_context_keys": ["end_user.id"]}
    ap = AuthProvider(id="ap_pu", tenant_id="t_pu", project_id="p_pu", name="idp",
                      kind="oauth2_authorization_code", config=cfg)
    ctx = {"end_user.id": "alice"}
    # The per-user name the callback computes must differ from the default single-account name.
    name = AuthResolver.bundle_secret_name("ap_pu", ctx, cfg["per_user_context_keys"])
    assert name != AuthResolver.bundle_secret_name("ap_pu")
    async with SessionLocal() as s:
        await SecretStore().write(s, tenant_id="t_pu", project_id="p_pu", name=name,
                                  value={"access_token": "ALICE", "expires_at": time.time() + 3600}, kind="oauth")
    # Alice's context resolves to her token...
    r = await AuthResolver().resolve(tenant_id="t_pu", project_id="p_pu", provider_id="ap_pu",
                                     provider=ap, context=ctx, force=True)
    assert r.headers["Authorization"] == "Bearer ALICE"
    # ...a different end-user's context does not (proving the bundle is genuinely per-user).
    with pytest.raises(KeyError):
        await AuthResolver().resolve(tenant_id="t_pu", project_id="p_pu", provider_id="ap_pu",
                                     provider=ap, context={"end_user.id": "bob"}, force=True)
