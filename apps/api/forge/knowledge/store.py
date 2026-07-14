"""Chroma-backed EmbeddingStore (the user-mandated vector store).

Embedded persistent client (no server). One collection, scoped by tenant_id +
project_id metadata so it's multi-tenant. We pass embeddings explicitly (our own
embedder), so Chroma never needs to download its default model - fully offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from forge.config import settings


@dataclass
class Hit:
    id: str
    text: str
    score: float
    metadata: dict
    # In hybrid mode `score` is the normalized RRF fusion RANK (top≈1.0), NOT cosine, so a
    # cosine floor (min_score) can't be applied to it. `vector_score` carries the underlying
    # dense cosine similarity for the SAME chunk so callers can threshold the true scale (see
    # nodes/rag.py). None when the chunk surfaced from BM25 only (no dense score to compare).
    vector_score: float | None = None


def _where(tenant_id: str, project_id: str, source_ids: list[str] | None) -> dict:
    clauses: list[dict] = [{"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}}]
    if source_ids:
        clauses.append({"source_id": {"$in": list(source_ids)}})
    return {"$and": clauses} if len(clauses) > 1 else clauses[0]


def citation_for(metadata: dict | None) -> str:
    """A short human-readable citation for a retrieved chunk, built from the source
    provenance now persisted in chunk metadata (see services.knowledge.ingest). Prefers a
    crawled page's URL/title, then the source name, then the source URI. Empty string when
    no provenance is available (legacy chunks ingested before provenance was recorded), so
    callers can omit the citation rather than print a blank one."""
    m = metadata or {}
    page_url = m.get("page_url")
    title = m.get("page_title") or m.get("source_name")
    if page_url:
        return f"{title} — {page_url}" if title and title != page_url else str(page_url)
    name = m.get("source_name")
    uri = m.get("source_uri")
    if name and uri:
        return f"{name} — {uri}"
    return str(name or uri or "")


# Client + collection handles are cached process-wide: PersistentClient construction
# and get_or_create_collection cost ~9s cold / ~10ms warm each (measured), and were
# previously paid on every store call.
_CLIENT_CACHE: dict[str, object] = {}
_COL_CACHE: dict[tuple[str, str], object] = {}

# Per-collection write version, bumped on every upsert/delete. The BM25 cache stamps the
# version it was built at and rebuilds when the collection changes, so a lexical index is
# never served stale after an ingest/delete - and never rebuilt while the corpus is unchanged.
_COL_VERSION: dict[tuple[str, str], int] = {}
# Cached lexical index per (collection-key, where-clause): {key: (version, bm25, ids, by_id)}.
# Building it (a full corpus scan + BM25 tokenization) previously ran on EVERY hybrid query
# and on the event loop; now it is built once per corpus version and reused (see hybrid_query,
# which itself runs off the loop via services.knowledge.search).
_BM25_CACHE: dict[tuple, tuple] = {}
# Max chunks pulled into the lexical corpus. Bounds the one-time build cost + cache memory;
# lexical matches in chunks beyond this cap are invisible (rare - needs a huge single project).
_CORPUS_CAP = 5000


def _bump_version(key: tuple[str, str]) -> None:
    _COL_VERSION[key] = _COL_VERSION.get(key, 0) + 1


# --- Backend-agnostic hybrid search --------------------------------------------------------
# The lexical (BM25) index build + the RRF fusion are identical regardless of which vector
# backend supplies the dense hits and the corpus scan, so they live here as free functions
# operating on any store that exposes `_key`, `query_where`, and `_get_documents`. Both
# ChromaStore and PgVectorStore delegate to them (DRY + a single caching path).


def _build_lexical_index(store, where: dict, corpus_cap: int) -> tuple:
    """Cached (bm25, ids, by_id) lexical index for `store`'s collection + where-clause. The
    corpus scan and BM25 tokenization are the expensive parts of a hybrid query; caching them
    per corpus-version turns every subsequent hybrid query into a cheap `get_scores` (rebuilt
    only after an ingest/delete bumps the version). by_id keeps chunk text + metadata so a
    BM25-only hit still carries its content."""
    from forge.knowledge.hybrid import build_bm25

    version = _COL_VERSION.get(store._key, 0)
    cache_key = (store._key, json.dumps(where, sort_keys=True))
    cached = _BM25_CACHE.get(cache_key)
    if cached is not None and cached[0] == version:
        return cached[1], cached[2], cached[3]
    corpus = store._get_documents(where, limit=corpus_cap)
    built = build_bm25([(h.id, h.text) for h in corpus])
    bm25, ids = built if built else (None, [])
    by_id = {h.id: h for h in corpus}
    _BM25_CACHE[cache_key] = (version, bm25, ids, by_id)
    return bm25, ids, by_id


def _hybrid_fuse(store, *, embedding, query: str, where: dict, top_k: int,
                 candidate_pool: int | None, corpus_cap: int) -> list[Hit]:
    """Fuse dense (vector) and lexical (BM25) ranking via RRF over `store`'s where-scoped
    corpus. Degrades to vector-only when BM25 is unavailable or the corpus has nothing to
    match. The returned `score` is the fused rank normalized to (0, 1] (NOT cosine); each Hit
    ALSO carries `vector_score`, the underlying dense cosine, so a caller's cosine floor
    thresholds the right scale."""
    from forge.knowledge.hybrid import bm25_scores, rrf_fuse

    pool = candidate_pool or max(top_k * 5, 20)
    vec_hits = store.query_where(embedding=embedding, where=where, top_k=pool)
    vec_cos = {h.id: h.score for h in vec_hits}  # dense cosine per chunk id
    bm25, ids, by_id = _build_lexical_index(store, where, corpus_cap)
    bm25_ids = bm25_scores((bm25, ids), query)
    if not bm25_ids:
        return [replace(h, vector_score=h.score) for h in vec_hits[:top_k]]

    fused = rrf_fuse([h.id for h in vec_hits], bm25_ids)
    by_id = dict(by_id)
    by_id.update({h.id: h for h in vec_hits})  # prefer the vector hit's text/metadata
    max_score = max(fused.values()) or 1.0
    ranked = sorted(fused, key=lambda i: fused[i], reverse=True)[:top_k]
    return [
        Hit(id=by_id[i].id, text=by_id[i].text,
            score=round(fused[i] / max_score, 4), metadata=by_id[i].metadata,
            vector_score=vec_cos.get(i))
        for i in ranked if i in by_id
    ]


def _client_for(path: str):
    client = _CLIENT_CACHE.get(path)
    if client is None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        client = chromadb.PersistentClient(
            path=path, settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True)
        )
        _CLIENT_CACHE[path] = client
    return client


class ChromaStore:
    def __init__(self, path: str | None = None, collection: str = "forge_kb") -> None:
        path = path or settings.chroma_path
        # Collection is keyed by embedder dimension (e.g. forge_kb_256 / forge_kb_1536)
        # so different embedders never collide on a fixed-dim collection.
        col = _COL_CACHE.get((path, collection))
        if col is None:
            col = _client_for(path).get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})
            _COL_CACHE[(path, collection)] = col
        self._client = _CLIENT_CACHE[path]
        self._col = col
        self._key = (path, collection)

    def upsert(self, *, ids, embeddings, documents, metadatas) -> None:
        if not ids:
            return
        self._col.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
        _bump_version(self._key)  # invalidate any cached lexical index for this collection

    def query(self, *, embedding, tenant_id, project_id, top_k=5, source_ids=None) -> list[Hit]:
        return self.query_where(
            embedding=embedding, where=_where(tenant_id, project_id, source_ids), top_k=top_k
        )

    def query_where(self, *, embedding, where: dict, top_k: int = 5) -> list[Hit]:
        res = self._col.query(query_embeddings=[embedding], n_results=top_k, where=where)
        hits: list[Hit] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, _id in enumerate(ids):
            dist = dists[i] if i < len(dists) else 0.0
            hits.append(Hit(id=_id, text=docs[i], score=1.0 - float(dist), metadata=metas[i] or {}))
        return hits

    def _get_documents(self, where: dict, limit: int | None = None) -> list[Hit]:
        """All stored chunks matching `where` (no vector query) - the corpus a lexical
        index is built over. score is 0.0 (unranked); `limit` caps the scan."""
        try:
            res = self._col.get(where=where, limit=limit) if limit else self._col.get(where=where)
        except Exception:  # noqa: BLE001 - collection empty / not ready
            return []
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        return [
            Hit(id=ids[i], text=(docs[i] if i < len(docs) else "") or "",
                score=0.0, metadata=(metas[i] if i < len(metas) else {}) or {})
            for i in range(len(ids))
        ]

    def hybrid_query(
        self, *, embedding, query: str, tenant_id, project_id, top_k=5, source_ids=None,
        candidate_pool: int | None = None, corpus_cap: int = _CORPUS_CAP,
    ) -> list[Hit]:
        """Fuse dense (vector) and lexical (BM25) ranking via RRF, scoped by the SAME
        tenant/project/source where-clause as vector search (see module `_hybrid_fuse`)."""
        return _hybrid_fuse(
            self, embedding=embedding, query=query, where=_where(tenant_id, project_id, source_ids),
            top_k=top_k, candidate_pool=candidate_pool, corpus_cap=corpus_cap,
        )

    def dump(self, where: dict, limit: int | None = None, *, ids: list[str] | None = None) -> dict:
        """Raw rows INCLUDING their embedding vectors - the input to the chunk map's
        dimensionality reduction. Returns {ids, documents, metadatas, embeddings} (parallel
        lists); embeddings come back as whatever numpy-ish rows Chroma stores. Empty on error.

        Pass `ids` to fetch exactly those rows (used to pull in specific retrieved chunks that
        fell outside the sampled `limit` window); otherwise fetch up to `limit` rows matching
        `where`."""
        include = ["embeddings", "documents", "metadatas"]
        try:
            if ids:
                res = self._col.get(ids=ids, include=include)
            else:
                res = self._col.get(where=where, limit=limit, include=include)
        except Exception:  # noqa: BLE001 - collection empty / not ready
            return {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
        return {
            "ids": res.get("ids") or [],
            "documents": res.get("documents") or [],
            "metadatas": res.get("metadatas") or [],
            "embeddings": res.get("embeddings") if res.get("embeddings") is not None else [],
        }

    def delete_ids(self, ids: list[str]) -> None:
        if ids:
            self._col.delete(ids=ids)
            _bump_version(self._key)

    def delete_by_source(self, source_id: str, *, tenant_id: str | None = None, project_id: str | None = None) -> None:
        clauses: list[dict] = [{"source_id": {"$eq": source_id}}]
        if tenant_id:
            clauses.append({"tenant_id": {"$eq": tenant_id}})
        if project_id:
            clauses.append({"project_id": {"$eq": project_id}})
        where = {"$and": clauses} if len(clauses) > 1 else clauses[0]
        self._col.delete(where=where)
        _bump_version(self._key)

    def delete_where(self, where: dict) -> None:
        """Delete every chunk matching an arbitrary where-clause (used by project deletion to
        sweep a tenant/project's vectors). Public so callers never poke at `._col` directly."""
        self._col.delete(where=where)
        _bump_version(self._key)

    def count(self, tenant_id: str, project_id: str) -> int:
        return self.count_where(_where(tenant_id, project_id, None))

    def count_where(self, where: dict) -> int:
        # include=[] returns ids only (ids always come back) - Chroma otherwise also materializes
        # every matching row's documents+metadatas just to be counted.
        try:
            return len(self._col.get(where=where, include=[]).get("ids", []))
        except Exception:  # noqa: BLE001
            return 0

    def list_docs(self, where: dict) -> dict:
        """ids + documents + metadatas (NO embeddings) for `where` - lighter than dump() for
        operations that only need chunk text (e.g. exact-duplicate detection)."""
        try:
            res = self._col.get(where=where, include=["documents", "metadatas"])
        except Exception:  # noqa: BLE001 - collection empty / not ready
            return {"ids": [], "documents": [], "metadatas": []}
        return {
            "ids": res.get("ids") or [],
            "documents": res.get("documents") or [],
            "metadatas": res.get("metadatas") or [],
        }

    def get_texts(self, ids: list[str], where: dict) -> dict:
        """Documents + metadatas for specific `ids`, ADDITIONALLY constrained by `where` - so a
        caller-supplied id can't read a row outside its tenant/project. NO embeddings (lighter
        than dump()); backs the chunk-map detail panel's on-demand full-text fetch."""
        if not ids:
            return {"ids": [], "documents": [], "metadatas": []}
        try:
            res = self._col.get(ids=ids, where=where, include=["documents", "metadatas"])
        except Exception:  # noqa: BLE001 - collection empty / not ready
            return {"ids": [], "documents": [], "metadatas": []}
        return {
            "ids": res.get("ids") or [],
            "documents": res.get("documents") or [],
            "metadatas": res.get("metadatas") or [],
        }

    def ids_where(self, where: dict) -> list[str]:
        """The ids currently stored matching `where` - lets a caller index only the rows
        that are actually MISSING (vs. a count comparison that can't detect stale ids)."""
        try:
            return list(self._col.get(where=where).get("ids", []))
        except Exception:  # noqa: BLE001 - collection empty / not ready
            return []


def make_store(collection: str = "forge_kb"):
    """Return the configured embedding store for `collection`, keyed by embedder dim by the
    caller (e.g. forge_kb_384). `settings.vector_backend` selects the backend: "chroma"
    (embedded, single-writer) or "pgvector" (Postgres-backed, shared across workers). Both
    expose the same interface, so call sites are backend-agnostic."""
    if settings.vector_backend == "pgvector":
        from forge.knowledge.pgvector_store import PgVectorStore

        return PgVectorStore(collection=collection)
    return ChromaStore(collection=collection)
