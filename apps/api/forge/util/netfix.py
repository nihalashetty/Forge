"""Outbound-DNS latency fix: suppress the slow IPv6 (AAAA) lookup.

Docker's embedded DNS resolver (127.0.0.11), and some cloud hosts with partial IPv6, do
not answer the AAAA (IPv6) query promptly for a hostname that publishes no IPv6 address -
they STALL until a ~4-5s timeout and only then fall back to the A record. So the FIRST
(cold) outbound call of every idle connection to such a host (api.openai.com, most REST-tool
endpoints, ...) paid a fixed multi-second DNS penalty; on a multi-hop run that dominated
end-to-end latency. Measured: a cold OpenAI call was 5.0s, vs 0.9s once AAAA is suppressed.

THE AUTHORITATIVE FIX is the process-level `RES_OPTIONS=no-aaaa` env var, set by
docker-compose for the api and worker services (glibc >= 2.36 reads it once at process start
and never sends AAAA). It works everywhere the resolution happens - including under uvloop,
whose C-level resolver BYPASSES a Python `socket.getaddrinfo` monkeypatch, and for asyncpg /
redis. The uvicorn server runs on uvloop, so the env var - not the monkeypatch - is what
actually fixes the server.

`install_prefer_ipv4_dns` is a best-effort *fallback* for run modes that don't get the env
var (e.g. a local `.venv` uvicorn): it (1) sets RES_OPTIONS in os.environ if unset - fully
effective on the default asyncio loop, only partial under uvloop because glibc caches the
resolver config per thread - and (2) wraps `socket.getaddrinfo` to resolve A (IPv4) first,
which covers the non-uvloop asyncio path. Both are no-ops / harmless when the env var is
already set. Gated by settings.prefer_ipv4_egress.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger("forge.util.netfix")

_orig_getaddrinfo = None  # set once, on install, so the patch is idempotent


def _ipv4_first_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002 - match socket API
    # Only intervene on AF_UNSPEC (0); a caller that explicitly asked for AF_INET6 still gets it.
    if family == socket.AF_UNSPEC:
        try:
            return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        except socket.gaierror:
            # No A record (e.g. an IPv6-only host, or an IPv6 literal): fall back to a v6 lookup
            # so it still resolves. A truly unknown host raises gaierror again and propagates.
            return _orig_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def install_prefer_ipv4_dns() -> bool:
    """Best-effort IPv6-lookup suppression for run modes without the RES_OPTIONS env var.

    Authoritative fix is `RES_OPTIONS=no-aaaa` in the process environment (docker-compose sets
    it); this only backstops other launch paths. Idempotent; returns True on the first install.
    """
    global _orig_getaddrinfo
    # (1) Ask glibc to stop sending AAAA at all. Fully effective only when this lands before the
    #     resolver initialises (i.e. set at process start); left as-is if already provided.
    os.environ.setdefault("RES_OPTIONS", "no-aaaa")
    # (2) IPv4-first at the Python resolver - covers the default asyncio loop (NOT uvloop, which
    #     resolves in C and ignores this); harmless when RES_OPTIONS already handled it.
    if _orig_getaddrinfo is not None:
        return False
    _orig_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = _ipv4_first_getaddrinfo  # type: ignore[assignment]
    log.info("prefer-IPv4 DNS fallback installed (authoritative fix is RES_OPTIONS=no-aaaa in the process env)")
    return True
