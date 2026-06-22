"""Process-wide shared httpx.AsyncClient.

Constructing an httpx client is expensive (~470ms measured on Windows: SSL context +
OS certificate-store load), and Forge previously built a fresh client per REST tool
call / webhook / auth token fetch / URL ingest - dominating per-node latency. One
shared client amortizes that cost; callers pass per-request `timeout=` (and
`follow_redirects=` where needed) instead of constructing clients.

The client is closed by the FastAPI lifespan on shutdown.
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def shared_async_client() -> httpx.AsyncClient:
    """The process-wide AsyncClient. Never close it at call sites - pass per-request
    `timeout=`/`follow_redirects=` overrides instead."""
    global _client
    if _client is None or _client.is_closed:
        # follow_redirects stays at httpx's default (False) to preserve REST-tool
        # semantics; fetch-style callers opt in per request.
        _client = httpx.AsyncClient(timeout=30)
    return _client


async def aclose_shared_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
