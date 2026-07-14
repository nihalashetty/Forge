"""SemanticCacheService - cache answers for semantically-similar questions.

For a high-volume support surface, most questions repeat. Caching by MEANING (vector
similarity over the question) lets paraphrases hit the cache, skipping a full LLM run.
Stored in a dim-keyed collection (`forge_cache_<dim>`) with the answer + timestamp in
metadata; TTL-checked on read. Scoped per project (+ optional `scope`).
"""

from __future__ import annotations

import hashlib
import time

from forge.config import settings
from forge.knowledge.store import ChromaStore
from forge.services.knowledge import KnowledgeService

# Cache defaults (env-overridable: FORGE_SEMANTIC_CACHE_THRESHOLD / _TTL_SECONDS). Threshold is
# deliberately HIGH: a wrong cached answer to a not-really-equivalent question is worse than a
# cache miss, so only near-identical paraphrases hit. Per-agent middleware config can override.
CACHE_DEFAULT_THRESHOLD = settings.semantic_cache_threshold
CACHE_DEFAULT_TTL_SECONDS = settings.semantic_cache_ttl_seconds


class SemanticCacheService:
    @staticmethod
    def _store(embedder) -> ChromaStore:
        return ChromaStore(collection=f"forge_cache_{embedder.dim}")

    @staticmethod
    def _id(tenant_id: str, project_id: str, scope: str, question: str) -> str:
        # Include tenant+project so two tenants asking the same question in the same scope
        # don't collide on one Chroma id and overwrite each other's cached answer (audit F-low).
        return hashlib.sha256(
            f"{tenant_id}::{project_id}::{scope}::{question.strip().lower()}".encode()
        ).hexdigest()[:32]

    @staticmethod
    async def lookup(session, tenant_id, project_id, question, *, scope="default", threshold=0.9, ttl=3600) -> str | None:
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        emb = await embedder.aembed_query(question)
        where = {"$and": [
            {"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}}, {"scope": {"$eq": scope}},
        ]}
        try:
            hits = SemanticCacheService._store(embedder).query_where(embedding=emb, where=where, top_k=1)
        except Exception:  # noqa: BLE001
            return None
        if not hits or hits[0].score < threshold:
            return None
        meta = hits[0].metadata or {}
        if ttl and (time.time() - float(meta.get("ts", 0))) > ttl:
            return None
        return meta.get("answer")

    @staticmethod
    async def store(session, tenant_id, project_id, question, answer, *, scope="default") -> None:
        if not answer:
            return
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        emb = await embedder.aembed_query(question)
        try:
            SemanticCacheService._store(embedder).upsert(
                ids=[SemanticCacheService._id(tenant_id, project_id, scope, question)], embeddings=[emb], documents=[question],
                metadatas=[{"tenant_id": tenant_id, "project_id": project_id, "scope": scope,
                            "answer": answer, "ts": time.time()}],
            )
        except Exception:  # noqa: BLE001 - cache write failure is non-fatal
            pass

    @staticmethod
    async def purge(session, tenant_id, project_id, *, scope: str | None = None,
                    ttl: int = CACHE_DEFAULT_TTL_SECONDS) -> int:
        """Delete cache entries older than `ttl` seconds for a tenant/project (optionally a
        single scope). TTL is checked on read too, but stale rows otherwise accumulate forever
        in Chroma (they still cost storage + widen every vector query), so this reclaims them.
        Returns how many were purged. Non-fatal: a store error yields 0. `ttl<=0` purges all."""
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        store = SemanticCacheService._store(embedder)
        clauses = [{"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}}]
        if scope is not None:
            clauses.append({"scope": {"$eq": scope}})
        where = {"$and": clauses} if len(clauses) > 1 else clauses[0]
        try:
            rows = store.list_docs(where)
        except Exception:  # noqa: BLE001
            return 0
        now = time.time()
        ids = rows.get("ids") or []
        metas = rows.get("metadatas") or []
        expired = [
            ids[i] for i in range(len(ids))
            if ttl <= 0 or (now - float((metas[i] or {}).get("ts", 0))) > ttl
        ]
        if not expired:
            return 0
        try:
            store.delete_ids(expired)
        except Exception:  # noqa: BLE001
            return 0
        return len(expired)
