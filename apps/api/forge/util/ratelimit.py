"""In-process rate limiting + idempotency (Redis swap documented for prod).

`RateLimiter` is a per-key token bucket; `IdempotencyCache` dedupes mutating
requests carrying an `Idempotency-Key`. Both are process-local — fine for a single
worker / dev; in a multi-worker prod deployment back them with Redis (same
interface) so limits/idempotency are shared across workers.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("forge.ratelimit")


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


class RedisRateLimiter:
    """Fixed-window counter shared across workers via Redis (same `allow` interface as the
    in-process limiter). Fails OPEN on a Redis error so a cache outage can't 500 every
    request. Uses the sync redis client — calls are single round-trips (sub-ms locally)."""

    def __init__(self, client) -> None:
        self._r = client

    def allow(self, key: str, *, rate: int, per: float = 60.0, burst: int | None = None) -> bool:
        if rate <= 0:
            return True
        window = int(time.time() // per)
        rk = f"forge:rl:{key}:{window}"
        try:
            pipe = self._r.pipeline()
            pipe.incr(rk)
            pipe.expire(rk, int(per) + 1)
            count = pipe.execute()[0]
            return int(count) <= int(burst or rate)
        except Exception:  # noqa: BLE001 - fail open on Redis trouble
            log.warning("redis rate-limit check failed for %s; allowing", key)
            return True


class RedisIdempotencyCache:
    """Cross-worker idempotency store (same `get`/`put` interface). Serializes the cached
    value as JSON (pydantic models via model_dump_json) and returns a dict on read."""

    def __init__(self, client, ttl_seconds: float = 600.0) -> None:
        self._r = client
        self._ttl = int(ttl_seconds)

    def get(self, key: str):
        try:
            raw = self._r.get(f"forge:idem:{key}")
        except Exception:  # noqa: BLE001
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None

    def put(self, key: str, value: object) -> None:
        try:
            payload = (
                value.model_dump_json() if hasattr(value, "model_dump_json")
                else json.dumps(value, default=str)
            )
            self._r.set(f"forge:idem:{key}", payload, ex=self._ttl)
        except Exception:  # noqa: BLE001
            pass


def _make_limiters():
    """Pick Redis-backed limiters when FORGE_REDIS_URL is set (shared across workers),
    else in-process (single-worker/dev). Same interface either way."""
    from forge.config import settings

    if settings.redis_url:
        try:
            import redis  # from the `workers` extra

            client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
            client.ping()
            log.info("rate-limit/idempotency backed by Redis")
            return RedisRateLimiter(client), RedisIdempotencyCache(client)
        except Exception as e:  # noqa: BLE001 - redis missing/unreachable -> in-process
            log.warning("FORGE_REDIS_URL set but Redis unavailable (%s); using in-process limiters", e)
    return RateLimiter(), IdempotencyCache()


# Process-wide singletons (Redis-backed when configured).
rate_limiter, idempotency = _make_limiters()
