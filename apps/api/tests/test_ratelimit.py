"""Rate limiter + idempotency cache unit tests."""

from __future__ import annotations

from forge.util.ratelimit import IdempotencyCache, RateLimiter


def test_token_bucket_blocks_after_burst():
    rl = RateLimiter()
    # rate=5/min, burst=5 → first 5 allowed, 6th blocked
    allowed = [rl.allow("k", rate=5, per=60, burst=5) for _ in range(6)]
    assert allowed == [True, True, True, True, True, False]


def test_zero_rate_is_unlimited():
    rl = RateLimiter()
    assert all(rl.allow("k", rate=0) for _ in range(100))


def test_keys_are_independent():
    rl = RateLimiter()
    assert rl.allow("a", rate=1, burst=1) is True
    assert rl.allow("a", rate=1, burst=1) is False
    assert rl.allow("b", rate=1, burst=1) is True  # different key unaffected


def test_idempotency_returns_stored_value():
    cache = IdempotencyCache(ttl_seconds=60)
    assert cache.get("x") is None
    cache.put("x", {"run_id": "r1"})
    assert cache.get("x") == {"run_id": "r1"}


def test_idempotency_expires():
    cache = IdempotencyCache(ttl_seconds=-1)  # already expired
    cache.put("x", 1)
    assert cache.get("x") is None
