"""Client-IP derivation shared by the request dependency (deps.client_ip) and the ASGI
audit middleware (audit_middleware._client_ip), so both apply the SAME reverse-proxy trust
rule and cannot drift apart - a drift is exactly how the audit path once believed
X-Forwarded-For unconditionally while the rate-limit path did not.
"""

from __future__ import annotations


def peer_is_trusted(peer: str | None, trusted_proxies: list[str]) -> bool:
    """Whether an X-Forwarded-For from this socket peer should be believed. Only configured
    reverse-proxy IPs are trusted; an empty list trusts none, so an arbitrary client can't
    spoof its IP for per-IP rate limits / audit. "*" trusts any peer (only safe behind an
    ingress that always overwrites XFF)."""
    if not trusted_proxies:
        return False
    if "*" in trusted_proxies:
        return True
    return peer in trusted_proxies


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, trusted_proxies: list[str]
) -> str | None:
    """Real client IP: the left-most X-Forwarded-For entry (the original client; proxies
    append on the right) when the socket peer is a trusted proxy, else the socket peer."""
    if forwarded_for and peer_is_trusted(peer, trusted_proxies):
        return forwarded_for.split(",")[0].strip()
    return peer
