"""Chroma-backed EmbeddingStore (the user-mandated vector store).

Embedded persistent client (no server). One collection, scoped by tenant_id +
project_id metadata so it's multi-tenant. We pass embeddings explicitly (our own
embedder), so Chroma never needs to download its default model — fully offline.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.config import settings


@dataclass
class Hit:
    id: str
    text: str
    score: float
    metadata: dict


def _where(tenant_id: str, project_id: str, source_ids: list[str] | None) -> dict:
    clauses: list[dict] = [{"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}}]
    if source_ids:
        clauses.append({"source_id": {"$in": list(source_ids)}})
    return {"$and": clauses} if len(clauses) > 1 else clauses[0]


# Client + collection handles are cached process-wide: PersistentClient construction
# and get_or_create_collection cost ~9s cold / ~10ms warm each (measured), and were
# previously paid on every store call.
_CLIENT_CACHE: dict[str, object] = {}
_COL_CACHE: dict[tuple[str, str], object] = {}


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

    def upsert(self, *, ids, embeddings, documents, metadatas) -> None:
        if not ids:
            return
        self._col.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

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
        """All stored chunks matching `where` (no vector query) — the corpus a lexical
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
        candidate_pool: int | None = None, corpus_cap: int = 5000,
    ) -> list[Hit]:
        """Fuse dense (vector) and lexical (BM25) ranking via RRF, scoped by the SAME
        tenant/project/source where-clause as vector search. Degrades to vector-only when
        BM25 is unavailable or the corpus has nothing to match. The returned score is the
        fused rank normalized to (0, 1] (NOT cosine) so downstream min_score still filters."""
        from forge.knowledge.hybrid import bm25_rank, rrf_fuse

        where = _where(tenant_id, project_id, source_ids)
        pool = candidate_pool or max(top_k * 5, 20)
        vec_hits = self.query_where(embedding=embedding, where=where, top_k=pool)
        corpus = self._get_documents(where, limit=corpus_cap)
        bm25_ids = bm25_rank(query, [(h.id, h.text) for h in corpus])
        if not bm25_ids:
            return vec_hits[:top_k]

        fused = rrf_fuse([h.id for h in vec_hits], bm25_ids)
        by_id: dict[str, Hit] = {h.id: h for h in corpus}
        by_id.update({h.id: h for h in vec_hits})  # prefer the vector hit's text/metadata
        max_score = max(fused.values()) or 1.0
        ranked = sorted(fused, key=lambda i: fused[i], reverse=True)[:top_k]
        return [
            Hit(id=by_id[i].id, text=by_id[i].text,
                score=round(fused[i] / max_score, 4), metadata=by_id[i].metadata)
            for i in ranked if i in by_id
        ]

    def delete_ids(self, ids: list[str]) -> None:
        if ids:
            self._col.delete(ids=ids)

    def delete_by_source(self, source_id: str, *, tenant_id: str | None = None, project_id: str | None = None) -> None:
        clauses: list[dict] = [{"source_id": {"$eq": source_id}}]
        if tenant_id:
            clauses.append({"tenant_id": {"$eq": tenant_id}})
        if project_id:
            clauses.append({"project_id": {"$eq": project_id}})
        where = {"$and": clauses} if len(clauses) > 1 else clauses[0]
        self._col.delete(where=where)

    def count(self, tenant_id: str, project_id: str) -> int:
        return self.count_where(_where(tenant_id, project_id, None))

    def count_where(self, where: dict) -> int:
        try:
            return len(self._col.get(where=where).get("ids", []))
        except Exception:  # noqa: BLE001
            return 0
