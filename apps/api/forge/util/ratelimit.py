"""In-process rate limiting + idempotency (Redis swap documented for prod).

`RateLimiter` is a per-key token bucket; `IdempotencyCache` dedupes mutating
requests carrying an `Idempotency-Key`. Both are process-local - fine for a single
worker / dev; in a multi-worker prod deployment back them with Redis (same
interface) so limits/idempotency are shared across workers.

`ResilientRateLimiter` (the exported `rate_limiter`) prefers Redis when
`FORGE_REDIS_URL` is set, transparently (re)connecting if Redis was down at boot or
drops later, and enforces limits in-process meanwhile. Keys on an anonymous/abuse-
sensitive PUBLIC surface (embed/*) fail CLOSED on a Redis error so a cache outage
can't silently drop the denial-of-wallet ceiling to unlimited (audit S2/finding b).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("forge.ratelimit")

# Keys on these prefixes belong to the anonymous public/embed surface: a Redis outage must
# FAIL CLOSED for them (deny) rather than dilute the shared limit to unlimited.
_PUBLIC_KEY_PREFIXES = ("embed:", "embed-ip:")

# In-process bucket-store bounds (finding b): stale/idle buckets are pruned on a timer and the
# store is hard-capped so an attacker spraying distinct keys can't grow it without limit.
_BUCKET_IDLE_TTL = 3600.0      # drop buckets untouched for this long
_BUCKET_MAX_KEYS = 100_000     # hard ceiling; evict least-recently-updated beyond this
_BUCKET_PRUNE_EVERY = 60.0     # min seconds between opportunistic sweeps


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token bucket: `rate` tokens refill per `per` seconds, capacity `burst`.

    The bucket store self-prunes: idle buckets (untouched for `_BUCKET_IDLE_TTL`) are swept on
    a timer and the store is hard-capped at `_BUCKET_MAX_KEYS` (least-recently-updated evicted),
    so a flood of distinct keys can't grow memory without bound (finding b)."""

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._last_prune = time.monotonic()

    def _prune_locked(self, now: float) -> None:
        if now - self._last_prune < _BUCKET_PRUNE_EVERY and len(self._buckets) <= _BUCKET_MAX_KEYS:
            return
        self._last_prune = now
        # Drop buckets idle longer than the TTL (a full, idle bucket is safe to recreate).
        stale = [k for k, b in self._buckets.items() if now - b.updated > _BUCKET_IDLE_TTL]
        for k in stale:
            self._buckets.pop(k, None)
        # Hard cap: evict the least-recently-updated keys if still over the ceiling.
        if len(self._buckets) > _BUCKET_MAX_KEYS:
            for k, _ in sorted(self._buckets.items(), key=lambda kv: kv[1].updated)[
                : len(self._buckets) - _BUCKET_MAX_KEYS
            ]:
                self._buckets.pop(k, None)

    def allow(self, key: str, *, rate: int, per: float = 60.0, burst: int | None = None) -> bool:
        if rate <= 0:
            return True  # 0/None => unlimited
        cap = float(burst or rate)
        refill = rate / per
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
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


def _redis_incr(client, key: str, *, rate: int, per: float, burst: int | None) -> bool:
    """One fixed-window increment against Redis. Raises on any Redis error (the caller decides
    fail-open/closed)."""
    window = int(time.time() // per)
    rk = f"forge:rl:{key}:{window}"
    pipe = client.pipeline()
    pipe.incr(rk)
    pipe.expire(rk, int(per) + 1)
    count = pipe.execute()[0]
    return int(count) <= int(burst or rate)


class RedisRateLimiter:
    """Fixed-window counter shared across workers via Redis (same `allow` interface as the
    in-process limiter). On a Redis error it fails OPEN by default (`fail_open=True`) so a
    cache outage can't 500 every request; pass `fail_open=False` for a limiter protecting the
    public surface, where a Redis outage should DENY rather than drop the limit."""

    def __init__(self, client, *, fail_open: bool = True) -> None:
        self._r = client
        self._fail_open = fail_open

    def allow(self, key: str, *, rate: int, per: float = 60.0, burst: int | None = None) -> bool:
        if rate <= 0:
            return True
        try:
            return _redis_incr(self._r, key, rate=rate, per=per, burst=burst)
        except Exception:  # noqa: BLE001 - Redis trouble: fail open/closed per config
            log.warning("redis rate-limit check failed for %s; %s", key, "allowing" if self._fail_open else "denying")
            return not self._fail_open


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


class _RedisConn:
    """Lazily (re)establishes a shared Redis client. If Redis is unset it's a permanent no-op;
    if it's configured but unavailable at boot, `get()` retries every `_RETRY_INTERVAL` seconds
    instead of falling back to per-worker in-process state forever (finding b)."""

    _RETRY_INTERVAL = 15.0

    def __init__(self, url: str | None) -> None:
        self.url = url
        self._client = None
        self._last_attempt = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def get(self):
        if not self.url or self._client is not None:
            return self._client
        now = time.monotonic()
        with self._lock:
            if self._client is not None:
                return self._client
            if now - self._last_attempt < self._RETRY_INTERVAL:
                return None
            self._last_attempt = now
            try:
                import redis  # from the `workers` extra

                client = redis.Redis.from_url(self.url, decode_responses=True)
                client.ping()
                self._client = client
                log.info("rate-limit/idempotency backed by Redis")
            except Exception as e:  # noqa: BLE001 - unreachable: keep in-process, retry later
                log.warning("FORGE_REDIS_URL set but Redis unavailable (%s); in-process fallback, will retry", e)
        return self._client

    def drop(self) -> None:
        with self._lock:
            self._client = None


class ResilientRateLimiter:
    """Rate limiter that prefers shared Redis and degrades safely (finding b):

    - No Redis configured  -> in-process token bucket (single-worker/dev).
    - Redis up             -> fixed-window shared across workers.
    - Redis down/dropped   -> PUBLIC (embed/*) keys fail CLOSED (deny); other keys fall back to
                              the in-process bucket (still bounded per-worker), never fully open.
                              The Redis handle is dropped so the next call transparently retries.
    """

    def __init__(self, conn: _RedisConn, *, force_public: bool = False) -> None:
        self._conn = conn
        self._local = RateLimiter()
        self._force_public = force_public

    def _is_public(self, key: str) -> bool:
        return self._force_public or key.startswith(_PUBLIC_KEY_PREFIXES)

    def allow(self, key: str, *, rate: int, per: float = 60.0, burst: int | None = None) -> bool:
        if rate <= 0:
            return True
        if self._conn.configured:
            client = self._conn.get()
            if client is not None:
                try:
                    return _redis_incr(client, key, rate=rate, per=per, burst=burst)
                except Exception:  # noqa: BLE001 - Redis dropped mid-flight
                    self._conn.drop()
                    if self._is_public(key):
                        log.warning("redis rate-limit failed for public key %s; denying", key)
                        return False
                    log.warning("redis rate-limit failed for %s; in-process fallback", key)
                    return self._local.allow(key, rate=rate, per=per, burst=burst)
            # Configured but currently unreachable.
            if self._is_public(key):
                return False  # fail CLOSED for the public/embed surface
            return self._local.allow(key, rate=rate, per=per, burst=burst)
        return self._local.allow(key, rate=rate, per=per, burst=burst)


class ResilientIdempotencyCache:
    """Idempotency cache that prefers shared Redis (lazy reconnect) and falls back in-process.
    Idempotency failing to in-process only risks a duplicate on a cache outage - acceptable, so
    this stays fail-soft (no fail-closed variant)."""

    def __init__(self, conn: _RedisConn, ttl_seconds: float = 600.0) -> None:
        self._conn = conn
        self._local = IdempotencyCache(ttl_seconds)
        self._ttl = int(ttl_seconds)

    def get(self, key: str):
        client = self._conn.get() if self._conn.configured else None
        if client is not None:
            return RedisIdempotencyCache(client, self._ttl).get(key)
        return self._local.get(key)

    def put(self, key: str, value: object) -> None:
        client = self._conn.get() if self._conn.configured else None
        if client is not None:
            RedisIdempotencyCache(client, self._ttl).put(key, value)
        else:
            self._local.put(key, value)


def _send_429(send):
    async def _do():
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [(b"content-type", b"application/json"), (b"retry-after", b"1")],
        })
        await send({"type": "http.response.body", "body": b'{"detail":"too many requests; slow down"}'})
    return _do()


class GlobalRateLimitMiddleware:
    """Coarse per-IP request ceiling (settings.api_rate_limit_per_minute) applied to every HTTP
    request as a blunt DoS guard, complementing the per-surface limits (runs/embed/auth/tools).
    Exempts liveness/readiness/metrics/docs and SSE stream paths (long-lived; EventSource
    auto-reconnects would otherwise be throttled) and OPTIONS preflight. Fails OPEN on limiter
    error - it's a coarse guard, not the security-critical embed ceiling (finding a)."""

    _EXEMPT_PREFIXES = ("/health", "/livez", "/readyz", "/metrics", "/version",
                        "/docs", "/openapi", "/redoc")

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from forge.config import settings

        rate = settings.api_rate_limit_per_minute
        method = scope.get("method")
        path = scope.get("path", "")
        if (rate <= 0 or method == "OPTIONS" or path.endswith("/stream")
                or any(path.startswith(p) for p in self._EXEMPT_PREFIXES)):
            return await self.app(scope, receive, send)
        from forge.util.clientip import resolve_client_ip

        headers = dict(scope.get("headers") or [])
        client = scope.get("client")
        peer = client[0] if client else None
        fwd = headers.get(b"x-forwarded-for")
        ip = resolve_client_ip(peer, fwd.decode("latin-1") if fwd else None, settings.trusted_proxies)
        try:
            ok = rate_limiter.allow(f"glbl:{ip or 'unknown'}", rate=rate, per=60, burst=rate)
        except Exception:  # noqa: BLE001 - coarse guard: never 500 on a limiter hiccup
            ok = True
        if not ok:
            return await _send_429(send)
        return await self.app(scope, receive, send)


# Process-wide singletons (Redis-backed when configured, with lazy reconnect + fail-closed
# public surface). `public_rate_limiter` is for NEW anonymous/public code that isn't keyed on an
# `embed:` prefix; the shared `rate_limiter` already fails closed for embed keys by prefix.
_redis_conn = _RedisConn(None)


def _init_limiters():
    from forge.config import settings

    global _redis_conn
    _redis_conn = _RedisConn(settings.redis_url)
    if _redis_conn.configured:
        _redis_conn.get()  # attempt an eager connect at boot (falls back + retries if down)
    return (
        ResilientRateLimiter(_redis_conn),
        ResilientIdempotencyCache(_redis_conn),
        ResilientRateLimiter(_redis_conn, force_public=True),
    )


rate_limiter, idempotency, public_rate_limiter = _init_limiters()
