"""Semantic response cache: paraphrased questions hit the cache."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.services.semantic_cache import SemanticCacheService

T, P = "t_sc", "p_sc"


async def test_store_then_lookup_hits_on_paraphrase():
    async with SessionLocal() as s:
        await SemanticCacheService.store(s, T, P, "What are your business hours?", "We're open 9am-5pm ET.")
    async with SessionLocal() as s:
        # near-duplicate question; fake embedder gives high overlap on shared words
        hit = await SemanticCacheService.lookup(s, T, P, "what are your business hours", threshold=0.6)
    assert hit == "We're open 9am-5pm ET."


async def test_lookup_miss_below_threshold():
    async with SessionLocal() as s:
        await SemanticCacheService.store(s, "t_m", "p_m", "How do I reset my password?", "Use the reset link.")
    async with SessionLocal() as s:
        hit = await SemanticCacheService.lookup(s, "t_m", "p_m", "completely unrelated rocket science", threshold=0.9)
    assert hit is None


async def test_ttl_expiry():
    async with SessionLocal() as s:
        await SemanticCacheService.store(s, "t_t", "p_t", "ping?", "pong")
    async with SessionLocal() as s:
        assert await SemanticCacheService.lookup(s, "t_t", "p_t", "ping?", threshold=0.5, ttl=-1) is None
