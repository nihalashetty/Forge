"""Embedders. The default is a local, open-source model via fastembed (ONNX, no API
key, no per-token cost) - it runs fully offline after a one-time model download. Set the
project's rag embedding_model to 'openai:text-embedding-3-*' (+ a key) for a hosted model.

There is deliberately no toy/hash fallback: if no real embedder can be built we raise a
clear error rather than silently returning meaningless vectors.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Protocol

from forge.tracing.tracer import embedding_span

log = logging.getLogger("forge.embeddings")


class Embedder(Protocol):
    name: str
    dim: int
    max_input_chars: int  # safe char budget before the model truncates (see ingest clamp)

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    async def aembed(self, texts: list[str]) -> list[list[float]]: ...
    async def aembed_query(self, text: str) -> list[float]: ...


# Embedding dimensions per known model (a Chroma collection is fixed-dim; the
# collection name is keyed by dim, so this must be right per model).
_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # fastembed (local ONNX) models - keyed by their model id (== the embedder.name we
    # store on a source), so embedding_health can spot a dim change.
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
}

# Default fastembed model when the ref is just "fastembed:" or unset (small, 384-dim, CPU-fast).
_DEFAULT_FASTEMBED = "BAAI/bge-small-en-v1.5"

# Max input sequence LENGTH (tokens) per model. An embedder silently TRUNCATES anything longer,
# so a chunk_size (in chars) that overflows this loses the tail of every chunk. Chunking is
# char-based, so we convert with a deliberately conservative chars/token ratio (dense text can
# be ~3-4 chars/token) to get a safe character budget - see `max_input_chars` + the ingest clamp.
_MODEL_MAX_TOKENS = {
    "text-embedding-3-small": 8191, "text-embedding-3-large": 8191, "text-embedding-ada-002": 8191,
    "BAAI/bge-small-en-v1.5": 512, "BAAI/bge-base-en-v1.5": 512, "BAAI/bge-large-en-v1.5": 512,
}
_DEFAULT_MAX_TOKENS = 512  # conservative fallback for an unmapped model
_CHARS_PER_TOKEN = 4  # rough English average; used only to derive a safe char budget from tokens


def _est_tokens(texts: list[str]) -> int:
    """Rough input-token estimate for pricing an embedding span (chars / ~4). Embedders don't
    return token counts, so this drives the span's cost via pricing; latency is always exact."""
    return sum(len(t or "") for t in texts) // _CHARS_PER_TOKEN


def _max_input_chars(model_name: str) -> int:
    return _MODEL_MAX_TOKENS.get(model_name, _DEFAULT_MAX_TOKENS) * _CHARS_PER_TOKEN

# Every embedding dim a Chroma collection may have been created under: the known model dims
# plus 256 (the removed hashed FakeEmbedder's legacy dim). delete/reingest sweep ALL of these
# (see services.knowledge._dim_collections) so switching embedders can't leave orphaned
# vectors behind - a stale FAQ would otherwise keep deflecting, a stale chunk keep surfacing.
KNOWN_EMBEDDING_DIMS: frozenset[int] = frozenset({256, *_MODEL_DIMS.values()})

# Relevance floors calibrated to the DEFAULT local BGE embedder (repo audit + measured cosines):
# BGE query/doc cosine for RELATED pairs ~0.75-0.86, UNRELATED ~0.38-0.52, so a 0.6 floor cleanly
# separates them and lets a wildly off-topic query surface NOTHING (the grounded agent then says
# it doesn't know instead of answering from the nearest chunk). Hosted models (e.g. OpenAI) sit
# on a LOWER cosine scale - set a smaller min_score per project/node when using them.
DEFAULT_MIN_SCORE = 0.6
# Cross-encoder rerank floor on the sigmoid (0-1) scale. The default ms-marco reranker is sharply
# bimodal (relevant ~1.0, irrelevant ~0.0 measured), so 0.3 drops off-topic while keeping
# borderline-relevant passages. Applied ONLY when rerank is on (a different scale from cosine).
DEFAULT_RERANK_MIN_SCORE = 0.3


class _LCEmbedder:
    """Adapter over a LangChain embeddings object (e.g. OpenAIEmbeddings)."""

    def __init__(self, emb, model_name: str) -> None:
        self._e = emb
        self.name = model_name
        self.dim = _MODEL_DIMS.get(model_name, 1536)
        self.max_input_chars = _max_input_chars(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._e.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._e.embed_query(text)

    # Async variants keep network embed calls off the event loop's back (the sync
    # ones block the loop - and the SSE stream - for the whole round trip).
    async def aembed(self, texts: list[str]) -> list[list[float]]:
        with embedding_span(self.name, n_texts=len(texts), input_tokens=_est_tokens(texts)):
            return await self._e.aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        with embedding_span(self.name, n_texts=1, input_tokens=_est_tokens([text])):
            return await self._e.aembed_query(text)


class _FastEmbedEmbedder:
    """Local, open-source embedder via fastembed (ONNX, no PyTorch, no API cost).

    Model files download once to the HuggingFace cache on first use, then run fully
    offline on CPU. Output dim is probed once at construction (it drives the dim-keyed
    Chroma collection). Instances are cached in _EMBEDDER_CACHE so the ~model-load cost
    is paid once per process.
    """

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        from forge.config import settings

        # A configured cache dir points at the model baked into the Docker image (offline,
        # no first-run download); None falls back to fastembed's own default temp cache.
        cache_dir = settings.fastembed_cache_dir or None
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self.name = model_name
        self.dim = len(next(iter(self._model.embed(["dim probe"]))))
        self.max_input_chars = _max_input_chars(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # fastembed yields numpy float32 arrays; Chroma wants plain float lists. `tolist()`
        # returns native Python floats in one C-level call (far cheaper than a per-element
        # `float(x)` comprehension over the vector).
        return [v.tolist() for v in self._model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]

    # fastembed is synchronous CPU work; run it in a thread so a batch embed doesn't
    # block the event loop (mirrors the _LCEmbedder rationale for network calls).
    async def aembed(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        with embedding_span(self.name, n_texts=len(texts), input_tokens=_est_tokens(texts)):
            return await asyncio.to_thread(self.embed, texts)

    async def aembed_query(self, text: str) -> list[float]:
        import asyncio

        with embedding_span(self.name, n_texts=1, input_tokens=_est_tokens([text])):
            return await asyncio.to_thread(self.embed_query, text)


# Provider embedder instances are expensive to construct (~1s measured on Windows:
# the OpenAI SDK builds two httpx clients = two SSL contexts), so cache per
# (model, key-fingerprint). The cache holds the client, not the key itself.
_EMBEDDER_CACHE: dict[tuple[str, str], Embedder] = {}
# Warn once per model when we fall back to the local default for a hosted-provider model
# - the dim-keyed collection then won't match content indexed under the hosted model.
_FALLBACK_WARNED: set[str] = set()


def _key_fp(api_key: str | None) -> str:
    if not api_key:
        return "env"
    # Not a security hash: this only namespaces the in-process embedder cache by key, so the
    # plaintext key never becomes a dict key. usedforsecurity=False documents that intent.
    return hashlib.sha256(api_key.encode(), usedforsecurity=False).hexdigest()[:16]


def _warn_once(model: str, msg: str) -> None:
    if model not in _FALLBACK_WARNED:
        _FALLBACK_WARNED.add(model)
        log.warning("resolve_embedder: %s (model=%r)", msg, model)


def _resolve_fastembed(name: str) -> Embedder:
    """Build (or reuse) a local fastembed embedder. Raises RuntimeError with an
    actionable message if fastembed / the model isn't available - there is no toy
    fallback, so callers surface a clear error instead of silently-wrong results."""
    cache_key = (f"fastembed:{name}", "fastembed")
    hit = _EMBEDDER_CACHE.get(cache_key)
    if hit is not None:
        return hit
    try:
        emb = _FastEmbedEmbedder(name)
    except Exception as e:  # noqa: BLE001 - fastembed missing / model download failed
        raise RuntimeError(
            f"Local embedder {name!r} unavailable: {e}. Install the 'knowledge' extra "
            "(fastembed) and ensure the model can be downloaded, or set an OpenAI embedding "
            "model + API key in project settings."
        ) from e
    _EMBEDDER_CACHE[cache_key] = emb
    return emb


def resolve_embedder(model: str | None = None, api_key: str | None = None) -> Embedder:
    """Return an embedder for the given model ref + (project) key.

    Default (unset or 'fastembed:<model>') is a local open-source ONNX embedder - no key,
    no API cost, offline after a one-time model download. 'openai:text-embedding-3-*' uses
    a hosted OpenAI model when a key resolves (per-project key first, then OPENAI_API_KEY);
    without a key, or on any construction failure, we fall back to the local default and
    warn once. A Chroma collection is fixed-dim, so we key it by `embedder.dim` (switching
    models needs a re-embed). Instances are cached.
    """
    # Local open-source default (also the ':'-only or unset ref).
    if not model or model.startswith("fastembed:"):
        name = model.split(":", 1)[1].strip() if (model and ":" in model) else ""
        return _resolve_fastembed(name or _DEFAULT_FASTEMBED)

    if model.startswith("openai:"):
        if api_key or os.environ.get("OPENAI_API_KEY"):
            cache_key = (model, _key_fp(api_key))
            hit = _EMBEDDER_CACHE.get(cache_key)
            if hit is not None:
                return hit
            try:
                from langchain_openai import OpenAIEmbeddings

                name = model.split(":", 1)[1]
                kwargs: dict = {"model": name}
                if api_key:
                    kwargs["api_key"] = api_key
                emb = _LCEmbedder(OpenAIEmbeddings(**kwargs), name)
                _EMBEDDER_CACHE[cache_key] = emb
                return emb
            except Exception:  # noqa: BLE001 - fall back to the local default (but make it visible)
                _warn_once(model, "could not construct OpenAIEmbeddings; falling back to local fastembed (dim differs - re-embed)")
                return _resolve_fastembed(_DEFAULT_FASTEMBED)
        # A hosted model was requested but no key resolved. Falling back to the local
        # default indexes/queries a DIFFERENT (dim-keyed) collection than the hosted model
        # would - the dim-flip trap that makes RAG/Q&A go quietly empty. Make it loud (once).
        _warn_once(model, "no OpenAI API key resolved; falling back to local fastembed (dim differs - re-embed)")
        return _resolve_fastembed(_DEFAULT_FASTEMBED)

    # Unrecognized provider prefix -> local default rather than a hard failure.
    _warn_once(model, "unrecognized embedding model; falling back to local fastembed")
    return _resolve_fastembed(_DEFAULT_FASTEMBED)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
