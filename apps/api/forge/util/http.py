"""Process-wide shared httpx.AsyncClient.

Constructing an httpx client is expensive (~470ms measured on Windows: SSL context +
OS certificate-store load), and Forge previously built a fresh client per REST tool
call / webhook / auth token fetch / URL ingest - dominating per-node latency. One
shared client amortizes that cost; callers pass per-request `timeout=` (and
`follow_redirects=` where needed) instead of constructing clients.

The client is closed by the FastAPI lifespan on shutdown.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

log = logging.getLogger("forge.util.http")

_client: httpx.AsyncClient | None = None
_insecure_client: httpx.AsyncClient | None = None


def shared_async_client() -> httpx.AsyncClient:
    """The process-wide AsyncClient. Never close it at call sites - pass per-request
    `timeout=`/`follow_redirects=` overrides instead."""
    global _client
    if _client is None or _client.is_closed:
        # follow_redirects stays at httpx's default (False) to preserve REST-tool
        # semantics; fetch-style callers opt in per request.
        _client = httpx.AsyncClient(timeout=30)
    return _client


def insecure_async_client() -> httpx.AsyncClient:
    """A process-wide AsyncClient with TLS certificate verification DISABLED.

    Only for outbound tool calls that explicitly opt in via `tls_skip_verify` AND target a host
    the operator has allow-listed as a trusted internal target (FORGE_EGRESS_ALLOW_PRIVATE_HOSTS)
    - e.g. an internal/dev service with a self-signed cert. `select_client` enforces that gate;
    do NOT reach for this client directly for arbitrary/public hosts."""
    global _insecure_client
    if _insecure_client is None or _insecure_client.is_closed:
        _insecure_client = httpx.AsyncClient(timeout=30, verify=False)
    return _insecure_client


def select_client(url: str, *, skip_verify: bool, policy, override: httpx.AsyncClient | None = None):
    """Pick the outbound client for `url`.

    - An explicit `override` (e.g. a test/mock client) is always returned as-is.
    - Otherwise the verified shared client is used, UNLESS `skip_verify` is set AND `url`'s host is
      on the egress allow_private_hosts list, in which case the verification-disabled client is used.
    - `skip_verify` is IGNORED (verification stays on) for any host NOT on that list, so certificate
      checks can never be turned off for an arbitrary or public host - only for a host the operator
      has already declared a trusted internal target.
    """
    if override is not None:
        return override
    if skip_verify:
        host = urlparse(url).hostname or ""
        if policy is not None and policy.allows_private(host):
            # Audit trail: record every request that actually runs with verification disabled.
            log.warning("TLS certificate verification DISABLED for host %r (tls_skip_verify, allow-private)", host)
            return insecure_async_client()
        log.warning(
            "tls_skip_verify ignored for host %r: not on FORGE_EGRESS_ALLOW_PRIVATE_HOSTS "
            "(certificate verification kept ON)", host,
        )
    return shared_async_client()


async def aclose_shared_client() -> None:
    global _client, _insecure_client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    if _insecure_client is not None and not _insecure_client.is_closed:
        await _insecure_client.aclose()
    _insecure_client = None
