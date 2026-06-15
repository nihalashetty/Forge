"""In-process rate limiting + idempotency (Redis swap documented for prod).

`RateLimiter` is a per-key token bucket; `IdempotencyCache` dedupes mutating
requests carrying an `Idempotency-Key`. Both are process-local — fine for a single
worker / dev; in a multi-worker prod deployment back them with Redis (same
interface) so limits/idempotency are shared across workers.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token bucket: `rate` tokens refill per `per` seconds, capacity `burst`."""

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, *, rate: int, per: float = 60.0, burst: int | None = None) -> bool:
        if rate <= 0:
            return True  # 0/None => unlimited
        cap = float(burst or rate)
        refill = rate / per
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                self._buckets[key] = _Bucket(tokens=cap - 1.0, updated=now)
                return True
            b.tokens = min(cap, b.tokens + (now - b.updated) * refill)
            b.updated = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True
            return False


class IdempotencyCache:
    """Maps an idempotency key -> a stored result for a TTL window."""

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if not hit:
                return None
            ts, value = hit
            if now - ts > self._ttl:
                self._store.pop(key, None)
                return None
            return value

    def put(self, key: str, value: object) -> None:
        with self._lock:
            # opportunistic prune
            if len(self._store) > 5000:
                now = time.monotonic()
                self._store = {k: v for k, v in self._store.items() if now - v[0] <= self._ttl}
            self._store[key] = (time.monotonic(), value)


# Process-wide singletons.
rate_limiter = RateLimiter()
idempotency = IdempotencyCache()
