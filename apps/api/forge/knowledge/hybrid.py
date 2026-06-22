"""Hybrid retrieval helpers: BM25 lexical ranking + Reciprocal Rank Fusion (RRF).

Dense vectors catch paraphrase ("how long for a refund" ~ "return processing time");
BM25 catches exact/rare terms vectors blur (error codes, SKUs, proper nouns). RRF
combines the two ranked lists by *position*, so their very different score scales
(cosine ~[0,1] vs unbounded BM25) never have to be normalized against each other.

All pure functions + graceful degradation: if rank_bm25 isn't installed, bm25_rank
returns [] and the caller falls back to vector-only - hybrid never hard-fails.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")
_RRF_K = 60  # standard RRF damping constant (Cormack et al.)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def bm25_rank(query: str, docs: list[tuple[str, str]]) -> list[str]:
    """Rank (id, text) candidates by BM25 against ``query``; ids best-first, positives
    only. Returns [] when rank_bm25 is absent, the corpus is empty, or nothing matches."""
    if not docs:
        return []
    try:
        from rank_bm25 import BM25Okapi
    except Exception:  # noqa: BLE001 - knowledge extra not installed -> vector-only
        return []
    tokenized = [tokenize(t) for _, t in docs]
    if not any(tokenized):
        return []
    q = tokenize(query)
    if not q:
        return []
    scores = BM25Okapi(tokenized).get_scores(q)
    ranked = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
    return [docs[i][0] for i in ranked if scores[i] > 0]


def rrf_fuse(*ranked_lists: list[str], k: int = _RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion over any number of ranked id lists -> {id: fused_score}."""
    fused: dict[str, float] = {}
    for ids in ranked_lists:
        for rank, _id in enumerate(ids):
            fused[_id] = fused.get(_id, 0.0) + 1.0 / (k + rank + 1)
    return fused
