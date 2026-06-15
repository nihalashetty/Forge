"""Embedders. Offline default is a deterministic hashed bag-of-words embedder so RAG
works with no API key or network; cosine similarity still reflects word overlap.
Swap to a real provider embedder by setting the project's rag embedding_model + key.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    async def aembed(self, texts: list[str]) -> list[list[float]]: ...
    async def aembed_query(self, text: str) -> list[float]: ...


class FakeEmbedder:
    """Hashed words + char-trigrams -> L2-normalized vector. Deterministic, offline.

    Trigrams give fuzzy similarity (e.g. 'refund' ~ 'refunds') so cosine scores are
    meaningful without a real model. Swap to a provider embedder for production quality.
    """

    name = "fake-hash-256"
    dim = 256

    def _features(self, text: str) -> list[str]:
        feats: list[str] = []
        for w in _WORD.findall((text or "").lower()):
            feats.append(w)
            s = f"#{w}#"
            feats.extend(s[i : i + 3] for i in range(len(s) - 2))  # boundary char trigrams
        return feats

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in self._features(text):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


# Embedding dimensions per known model (a Chroma collection is fixed-dim; the
# collection name is keyed by dim, so this must be right per model).
_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class _LCEmbedder:
    """Adapter over a LangChain embeddings object (e.g. OpenAIEmbeddings)."""

    def __init__(self, emb, model_name: str) -> None:
        self._e = emb
        self.name = model_name
        self.dim = _MODEL_DIMS.get(model_name, 1536)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._e.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._e.embed_query(text)

    # Async variants keep network embed calls off the event loop's back (the sync
    # ones block the loop — and the SSE stream — for the whole round trip).
    async def aembed(self, texts: list[str]) -> list[list[float]]:
        return await self._e.aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await self._e.aembed_query(text)


# Provider embedder instances are expensive to construct (~1s measured on Windows:
# the OpenAI SDK builds two httpx clients = two SSL contexts), so cache per
# (model, key-fingerprint). The cache holds the client, not the key itself.
_EMBEDDER_CACHE: dict[tuple[str, str], Embedder] = {}
_FAKE = FakeEmbedder()


def _key_fp(api_key: str | None) -> str:
    if not api_key:
        return "env"
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def resolve_embedder(model: str | None = None, api_key: str | None = None) -> Embedder:
    """Return an embedder for the given model ref + (project) key.

    Uses a real provider embedder (e.g. 'openai:text-embedding-3-small') when the
    model is provider-prefixed AND a key is available — the per-project key first,
    then the env var. Otherwise the offline FakeEmbedder. A Chroma collection is
    fixed-dim, so we key the collection by `embedder.dim` (switching needs re-ingest).
    Instances are cached (construction is ~1s on Windows).
    """
    if model and model.startswith("openai:") and (api_key or os.environ.get("OPENAI_API_KEY")):
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
        except Exception:  # noqa: BLE001 - fall back to offline
            pass
    return _FAKE


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
