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
