"""Two-stage retrieval: a local cross-encoder re-ranker (stage 2).

Vector/hybrid search (stage 1) is fast but coarse - it scores every candidate against the
query independently. A cross-encoder reads the query AND a candidate *together*, so it judges
relevance far more accurately - at a cost, which is why it only ever runs over a small
shortlist (top-N) that stage 1 already narrowed down.

Fully local + offline, no new dependency: it reuses ``fastembed`` (already the default
embedder backend) via its ``TextCrossEncoder`` - ONNX on CPU, model files download once to the
same HuggingFace cache the embedders use. Like ``bm25_rank`` in hybrid.py, this NEVER hard-fails:
if the model can't load (knowledge extra absent / offline first run / bad model id) the input
order is returned unchanged and a warning is logged once. Re-ranking is opt-in per retrieval
node (or the search debugger); default retrieval is unchanged.
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from forge.knowledge.store import Hit

log = logging.getLogger("forge.rerank")

# Small, CPU-fast cross-encoder (~80 MB). BAAI/bge-reranker-base (~1 GB) is a heavier, higher-
# quality alternative a project can opt into via rag_defaults.reranker_model.
DEFAULT_RERANKER = "Xenova/ms-marco-MiniLM-L-6-v2"

# Warn once per model when a reranker can't be built, so a missing model degrades quietly to
# stage-1 order instead of spamming logs on every query.
_FALLBACK_WARNED: set[str] = set()


class Reranker(Protocol):
    name: str

    def scores(self, query: str, docs: list[str]) -> list[float]: ...


def _sigmoid(x: float) -> float:
    # Cross-encoder outputs are unbounded logits; squash to (0,1) so a re-ranked Hit.score
    # keeps the same 0..1 scale as cosine/fused scores and a downstream min_score still filters.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


class _FastEmbedReranker:
    """Adapter over fastembed's TextCrossEncoder (ONNX cross-encoder, CPU, offline)."""

    def __init__(self, model_name: str) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        from forge.config import settings

        # Reuse the same baked/offline model cache the embedders use (falls back to fastembed's
        # own default temp cache when unset).
        cache_dir = settings.fastembed_cache_dir or None
        self._model = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)
        self.name = model_name

    def scores(self, query: str, docs: list[str]) -> list[float]:
        # TextCrossEncoder.rerank yields one raw score per doc, in input order.
        return [float(s) for s in self._model.rerank(query, list(docs))]


# Rerankers are expensive to construct (ONNX model load); cache one per model id process-wide,
# mirroring the embedder cache in embeddings.py.
_RERANKER_CACHE: dict[str, Reranker] = {}


def _normalize_model(model: str | None) -> str:
    """Accept a bare id ('Xenova/...') or a 'fastembed:<id>' ref (matching the embedding_model
    convention); '' / None / ':'-only -> the default reranker."""
    if not model:
        return DEFAULT_RERANKER
    m = model.strip()
    if m.startswith("fastembed:"):
        m = m.split(":", 1)[1].strip()
    return m or DEFAULT_RERANKER


def resolve_reranker(model: str | None = None) -> Reranker | None:
    """Build (or reuse) a local cross-encoder re-ranker. Returns None (never raises) if the
    model can't be constructed, so callers fall back to stage-1 order."""
    name = _normalize_model(model)
    hit = _RERANKER_CACHE.get(name)
    if hit is not None:
        return hit
    try:
        rr = _FastEmbedReranker(name)
    except Exception as e:  # noqa: BLE001 - fastembed/model unavailable -> no rerank
        if name not in _FALLBACK_WARNED:
            _FALLBACK_WARNED.add(name)
            log.warning("resolve_reranker: %r unavailable (%s); using stage-1 order", name, e)
        return None
    _RERANKER_CACHE[name] = rr
    return rr


def rerank_hits(query: str, hits: list[Hit], *, top_k: int, model: str | None = None) -> list[Hit]:
    """Re-order ``hits`` by cross-encoder relevance to ``query`` and keep the top ``top_k``.

    Each returned Hit's ``score`` is the sigmoid of the cross-encoder logit (0..1), so
    min_score filtering downstream still works. Degrades to ``hits[:top_k]`` (unchanged order)
    when there is nothing to do or the reranker can't be built - never hard-fails.
    """
    if not hits or not (query or "").strip():
        return hits[:top_k]
    reranker = resolve_reranker(model)
    if reranker is None:
        return hits[:top_k]
    try:
        raw = reranker.scores(query, [h.text for h in hits])
    except Exception:  # noqa: BLE001 - inference failure -> stage-1 order
        log.warning("rerank_hits: cross-encoder inference failed; using stage-1 order", exc_info=True)
        return hits[:top_k]
    # A score-count mismatch would let zip() silently drop the unpaired tail hits; treat it as a
    # failure and fall back to stage-1 order rather than returning a truncated/misaligned set.
    if len(raw) != len(hits):
        log.warning("rerank_hits: got %d scores for %d hits; using stage-1 order", len(raw), len(hits))
        return hits[:top_k]
    scored = sorted(
        (replace(h, score=round(_sigmoid(s), 4)) for h, s in zip(hits, raw, strict=True)),
        key=lambda h: h.score,
        reverse=True,
    )
    return scored[:top_k]


async def arerank_hits(query: str, hits: list[Hit], *, top_k: int, model: str | None = None) -> list[Hit]:
    """Async wrapper: run the (CPU-bound) cross-encoder off the event loop."""
    import asyncio

    return await asyncio.to_thread(rerank_hits, query, hits, top_k=top_k, model=model)
