"""`tls_skip_verify` is gated: TLS verification may be disabled only for a host explicitly on
FORGE_EGRESS_ALLOW_PRIVATE_HOSTS. For any other host the flag is ignored and verification stays on,
so certificate checks can never be turned off for an arbitrary/public endpoint."""

from __future__ import annotations

import httpx

from forge.util.http import (
    aclose_shared_client,
    insecure_async_client,
    select_client,
    shared_async_client,
)
from forge.util.ssrf import EgressPolicy


async def test_tls_skip_verify_is_gated_to_allow_private_hosts():
    policy = EgressPolicy(block_private=True, allow_private_hosts=("host.docker.internal",))
    try:
        # opted-in internal host + skip_verify -> verification-disabled client
        assert select_client(
            "https://host.docker.internal:9002/x", skip_verify=True, policy=policy
        ) is insecure_async_client()

        # public host + skip_verify -> IGNORED, stays on the verified shared client
        assert select_client(
            "https://api.example.com/x", skip_verify=True, policy=policy
        ) is shared_async_client()

        # allow-private host but skip_verify off -> verified shared client
        assert select_client(
            "https://host.docker.internal/x", skip_verify=False, policy=policy
        ) is shared_async_client()

        # an explicit client (e.g. a test/mock) always wins over the gate
        mock = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        try:
            assert select_client(
                "https://host.docker.internal/x", skip_verify=True, policy=policy, override=mock
            ) is mock
        finally:
            await mock.aclose()

        # suffix match honored: a subdomain of an allow-private parent is still gated-in
        policy2 = EgressPolicy(block_private=True, allow_private_hosts=("internal.corp",))
        assert select_client(
            "https://svc.internal.corp/x", skip_verify=True, policy=policy2
        ) is insecure_async_client()
    finally:
        await aclose_shared_client()


async def test_guarded_request_reselects_client_per_redirect_hop(monkeypatch):
    """A redirect off an allow-private host to a public host must re-select the client from the
    hop's OWN host, so verify-off (tls_skip_verify) never carries onto a non-allow-private hop.
    Regression for the MEDIUM finding that guarded_request reused one client across all hops."""
    import forge.util.ssrf as ssrf

    seen_hosts: list[str] = []

    async def _no_validate(url, policy=None):  # skip real DNS/SSRF resolution in the unit test
        return url

    def _handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "internal.corp":
            return httpx.Response(302, headers={"location": "https://public.example/final"})
        return httpx.Response(200, json={"ok": True})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    def _spy_select(url, *, skip_verify, policy, override=None):
        seen_hosts.append(httpx.URL(url).host)
        return mock

    monkeypatch.setattr(ssrf, "validate_url", _no_validate)
    monkeypatch.setattr("forge.util.http.select_client", _spy_select)

    policy = EgressPolicy(block_private=True, allow_private_hosts=("internal.corp",))
    try:
        r = await ssrf.guarded_request(
            None, "GET", "https://internal.corp/x", policy=policy, skip_verify=True, follow_redirects=True,
        )
        assert r.status_code == 200
    finally:
        await mock.aclose()

    # select_client is consulted per hop with that hop's own host, so the real select_client would
    # hand the public leg the VERIFIED client (proven by the gate test above), not the insecure one.
    assert seen_hosts == ["internal.corp", "public.example"]
