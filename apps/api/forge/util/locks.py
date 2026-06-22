"""Process-local keyed async locks + per-tenant concurrency gates.

Used to serialize runs that share a LangGraph thread (so concurrent turns can't
interleave checkpoint writes) and to bound concurrent in-flight runs per tenant.

These are in-process: correct for a single worker. In a multi-worker deployment
the same guarantees need a distributed lock / counter (Redis); the call sites are
written so that swap is a drop-in. See `forge.util.ratelimit` for the same pattern.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager


class KeyedLocks:
    """A registry of asyncio.Locks keyed by an arbitrary string.

    Locks are created lazily and kept for the process lifetime (the key space -
    thread ids / tenant ids - is bounded in practice and each lock is tiny).
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def acquire_cm(self, key: str):
        # Double-checked creation under a short guard so two coroutines asking for the
        # same key get the *same* Lock instance.
        lock = self._locks.get(key)
        if lock is None:
            async with self._guard:
                lock = self._locks.get(key)
                if lock is None:
                    lock = asyncio.Lock()
                    self._locks[key] = lock
        return lock


class ConcurrencyLimitExceeded(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TenantConcurrency:
    """Bounds the number of concurrent in-flight runs per tenant (best-effort, in-process)."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def slot(self, tenant_id: str, limit: int):
        """Reserve a concurrency slot for `tenant_id`. Raises ConcurrencyLimitExceeded
        when `limit` (>0) in-flight runs already exist. limit <= 0 => unlimited."""
        reserved = False
        if limit and limit > 0:
            async with self._guard:
                if self._counts[tenant_id] >= limit:
                    raise ConcurrencyLimitExceeded(
                        f"too many concurrent runs for this workspace ({limit}); retry shortly"
                    )
                self._counts[tenant_id] += 1
                reserved = True
        try:
            yield
        finally:
            if reserved:
                async with self._guard:
                    self._counts[tenant_id] = max(0, self._counts[tenant_id] - 1)


# Process-wide singletons.
thread_locks = KeyedLocks()      # serialize runs sharing a LangGraph thread
tenant_run_locks = KeyedLocks()  # serialize quota admission per tenant
tenant_concurrency = TenantConcurrency()
