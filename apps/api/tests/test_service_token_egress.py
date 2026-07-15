"""Server-to-server integration primitives:

1. A static service API token (`FORGE_SERVICE_API_TOKEN`) that authenticates a trusted backend
   as a least-privilege (editor) service identity - the outer "is this call from our backend"
   barrier. Non-expiring, revoked by rotation.
2. An egress allow-private-hosts toggle (`FORGE_EGRESS_ALLOW_PRIVATE_HOSTS`) that lets specific
   trusted internal hosts (localhost, on-prem services) be reached even while the SSRF guard's
   private-address block stays on globally (default-deny, explicit-allow).
"""

import pytest
from fastapi import HTTPException

from forge.config import settings
from forge.deps import get_current_user
from forge.util.ssrf import EgressBlocked, EgressPolicy, validate_url

# --- service token ---------------------------------------------------------------------------


class _State:
    tenant_id = "t-seed"


class _App:
    state = _State()


class _Req:
    def __init__(self, authorization: str | None):
        self.headers = {"authorization": authorization} if authorization else {}
        self.app = _App()


async def test_service_token_authenticates_as_editor(monkeypatch):
    monkeypatch.setattr(settings, "service_api_token", "svc-secret-abc123")
    user = await get_current_user(_Req("Bearer svc-secret-abc123"))
    assert user.role == "editor"          # least privilege, but enough to assert end_user identity
    assert user.tenant_id == "t-seed"     # bound to the seeded workspace
    assert user.is_fallback is False


async def test_wrong_service_token_is_rejected(monkeypatch):
    # A present-but-wrong token must NOT fall back to an anonymous/owner identity: it fails the
    # constant-time compare, then fails JWT decode -> 401.
    monkeypatch.setattr(settings, "service_api_token", "svc-secret-abc123")
    with pytest.raises(HTTPException) as e:
        await get_current_user(_Req("Bearer not-the-token"))
    assert e.value.status_code == 401


async def test_empty_service_token_setting_is_disabled(monkeypatch):
    # With no service token configured, the branch is inert (a random bearer is treated as a
    # would-be JWT and rejected).
    monkeypatch.setattr(settings, "service_api_token", "")
    with pytest.raises(HTTPException) as e:
        await get_current_user(_Req("Bearer anything"))
    assert e.value.status_code == 401


# --- egress allow-private-hosts --------------------------------------------------------------


async def test_private_host_blocked_by_default():
    with pytest.raises(EgressBlocked):
        await validate_url("http://127.0.0.1:9002/x", EgressPolicy(block_private=True))


async def test_allow_private_hosts_bypasses_block_by_ip():
    url = await validate_url(
        "http://127.0.0.1:9002/x",
        EgressPolicy(block_private=True, allow_private_hosts=("127.0.0.1",)),
    )
    assert url == "http://127.0.0.1:9002/x"


async def test_allow_private_hosts_bypasses_block_by_name():
    # `localhost` resolves to a loopback IP, but the host is explicitly allow-listed.
    url = await validate_url(
        "http://localhost:9002/quote/quoteDetail/1",
        EgressPolicy(block_private=True, allow_private_hosts=("localhost",)),
    )
    assert url.startswith("http://localhost")


async def test_non_allowlisted_private_still_blocked():
    with pytest.raises(EgressBlocked):
        await validate_url(
            "http://10.0.0.5/x",
            EgressPolicy(block_private=True, allow_private_hosts=("localhost",)),
        )


def test_from_settings_reads_global_allow_private(monkeypatch):
    monkeypatch.setattr(settings, "egress_allow_private_hosts", ["localhost", "127.0.0.1"])
    p = EgressPolicy.from_settings()
    assert "localhost" in p.allow_private_hosts and "127.0.0.1" in p.allow_private_hosts


def test_from_settings_ignores_project_allow_private(monkeypatch):
    # SECURITY (audit H1): project config is editable by any tenant member, so it must NOT be able
    # to add a private-host bypass. allow_private_hosts is a deployment-level control only.
    monkeypatch.setattr(settings, "egress_allow_private_hosts", [])
    p = EgressPolicy.from_settings({"allow_private_hosts": ["10.0.0.5"]})
    assert "10.0.0.5" not in p.allow_private_hosts
    assert p.allow_private_hosts == ()


def test_from_settings_project_cannot_loosen_block_private(monkeypatch):
    # SECURITY (audit H1): a project may only TIGHTEN block_private (False->True), never turn the
    # SSRF guard off. With the guard on globally, project block_private:false is ignored.
    monkeypatch.setattr(settings, "egress_block_private", True)
    assert EgressPolicy.from_settings({"block_private": False}).block_private is True
    # And a project can still tighten when the deployment default is off.
    monkeypatch.setattr(settings, "egress_block_private", False)
    assert EgressPolicy.from_settings({"block_private": True}).block_private is True
