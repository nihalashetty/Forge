"""SSRF egress guard.

Forge lets users (and the build assistant) point REST/GraphQL tools, webhooks,
`web_fetch`, URL knowledge ingestion, and auth/OAuth token fetches at arbitrary
URLs. On a hosted multi-tenant deployment that is a Server-Side Request Forgery
vector: a tenant could target `169.254.169.254` (cloud metadata creds),
`localhost`, or internal services.

`validate_url` resolves the host and rejects any URL that points at a private,
loopback, link-local, reserved, or cloud-metadata address, and enforces optional
per-deployment / per-project allow/deny host lists. `guarded_request` /
`guarded_get` wrap an httpx client and re-validate every redirect hop.

Residual caveat: DNS rebinding (host resolves public at validation, private at
connect) is not fully closed without IP-pinned transports; the per-hop redirect
validation and blocking of private ranges cover the overwhelmingly common cases.
Set `FORGE_EGRESS_BLOCK_PRIVATE=false` only for trusted single-tenant installs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from forge.config import settings


class EgressBlocked(ValueError):
    """Raised when a URL is not allowed to be requested (SSRF guard)."""


@dataclass(frozen=True)
class EgressPolicy:
    """Resolved egress policy for a request. Host lists match on exact host or any
    parent domain suffix (e.g. `example.com` allows `api.example.com`)."""

    block_private: bool = True
    allow_hosts: tuple[str, ...] = field(default_factory=tuple)
    deny_hosts: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_settings(cls, project_egress: dict | None = None) -> EgressPolicy:
        block = settings.egress_block_private
        allow = list(settings.egress_allow_hosts or [])
        deny = list(settings.egress_deny_hosts or [])
        if project_egress:
            if project_egress.get("block_private") is not None:
                block = bool(project_egress["block_private"])
            allow += list(project_egress.get("allow_hosts") or [])
            deny += list(project_egress.get("deny_hosts") or [])
        return cls(block_private=block, allow_hosts=tuple(allow), deny_hosts=tuple(deny))


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    for p in patterns:
        p = (p or "").lower().lstrip("*.").rstrip(".")
        if not p:
            continue
        if host == p or host.endswith("." + p):
            return True
    return False


def _ip_is_blocked(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # un-parseable -> refuse
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) — unwrap and re-check the v4 address.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local  # 169.254.0.0/16 incl. cloud metadata
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv6Address) and addr.is_site_local)
    )


async def _resolve_ips(host: str, port: int) -> set[str]:
    # If the host is already a literal IP, getaddrinfo returns it unchanged.
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise EgressBlocked(f"could not resolve host {host!r}: {e}") from e
    return {ai[4][0] for ai in infos}


async def validate_url(url: str, policy: EgressPolicy | None = None) -> str:
    """Raise EgressBlocked if `url` may not be requested; return it otherwise."""
    policy = policy or EgressPolicy.from_settings()
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise EgressBlocked(f"blocked URL scheme {scheme!r} (only http/https allowed)")
    host = parsed.hostname
    if not host:
        raise EgressBlocked("URL has no host")

    if _host_matches(host, policy.deny_hosts):
        raise EgressBlocked(f"host {host!r} is on the egress deny list")
    if policy.allow_hosts and not _host_matches(host, policy.allow_hosts):
        raise EgressBlocked(f"host {host!r} is not on the egress allow list")

    if policy.block_private:
        port = parsed.port or (443 if scheme == "https" else 80)
        for ip in await _resolve_ips(host, port):
            if _ip_is_blocked(ip):
                raise EgressBlocked(f"host {host!r} resolves to blocked address {ip}")
    return url


async def validate_host_port(host: str | None, port: int, policy: EgressPolicy | None = None) -> None:
    """Apply the egress policy to a non-HTTP network target (e.g. a database DSN host) so
    the SSRF guard isn't limited to HTTP tools (audit S7). Raises EgressBlocked when the
    host is denied / not allow-listed / resolves to a private/loopback/metadata address."""
    policy = policy or EgressPolicy.from_settings()
    host = (host or "").lower().rstrip(".")
    if not host:
        return  # no network host (e.g. a local sqlite file) — nothing to guard
    if _host_matches(host, policy.deny_hosts):
        raise EgressBlocked(f"host {host!r} is on the egress deny list")
    if policy.allow_hosts and not _host_matches(host, policy.allow_hosts):
        raise EgressBlocked(f"host {host!r} is not on the egress allow list")
    if policy.block_private:
        for ip in await _resolve_ips(host, port or 0):
            if _ip_is_blocked(ip):
                raise EgressBlocked(f"host {host!r} resolves to blocked address {ip}")


async def guarded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    policy: EgressPolicy | None = None,
    follow_redirects: bool = False,
    max_redirects: int = 5,
    **kwargs,
) -> httpx.Response:
    """Validate `url` (and every redirect hop) before connecting, then request it.

    httpx's own redirect-following is never enabled (it would connect to a hop without
    the egress check). Instead each hop is fetched with `follow_redirects=False`, and the
    follow-up is driven from httpx's `response.next_request` — which httpx already builds
    with cross-origin `Authorization`/`Cookie` stripping (so a redirect to a foreign host
    cannot exfiltrate the caller's credentials), relative-Location resolution, and the
    correct method/body downgrade. We add only the per-hop `validate_url` httpx lacks.

    `next_request` is populated solely for genuine redirects (301/302/303/307/308 with a
    Location), so 300/304/305/306 fall through as terminal responses. The redirect chain
    is attached to the final response's `.history`, so callers can read `str(resp.url)`
    (final URL) and `[str(h.url) for h in resp.history]` (the hops) uniformly.
    """
    policy = policy or EgressPolicy.from_settings()
    await validate_url(url, policy)
    resp = await client.request(method, url, follow_redirects=False, **kwargs)
    if not follow_redirects:
        return resp

    history: list[httpx.Response] = []
    for _ in range(max_redirects):
        nxt = resp.next_request
        if nxt is None:  # terminal (non-redirect) response
            break
        await validate_url(str(nxt.url), policy)
        history.append(resp)
        resp = await client.send(nxt, follow_redirects=False)
    if resp.next_request is not None:  # still redirecting after the cap
        raise EgressBlocked(f"too many redirects following {url!r}")
    if history:
        resp.history = history
    return resp


async def guarded_get(client: httpx.AsyncClient, url: str, *, policy=None, **kwargs) -> httpx.Response:
    return await guarded_request(client, "GET", url, policy=policy, **kwargs)
