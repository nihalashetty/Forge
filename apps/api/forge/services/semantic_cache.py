"""SemanticCacheService — cache answers for semantically-similar questions.

For a high-volume support surface, most questions repeat. Caching by MEANING (vector
similarity over the question) lets paraphrases hit the cache, skipping a full LLM run.
Stored in a dim-keyed collection (`forge_cache_<dim>`) with the answer + timestamp in
metadata; TTL-checked on read. Scoped per project (+ optional `scope`).
"""

from __future__ import annotations

import hashlib
import time

from forge.knowledge.store import ChromaStore
from forge.services.knowledge import KnowledgeService


class SemanticCacheService:
    @staticmethod
    def _store(embedder) -> ChromaStore:
        return ChromaStore(collection=f"forge_cache_{embedder.dim}")

    @staticmethod
    def _id(scope: str, question: str) -> str:
        return hashlib.sha256(f"{scope}::{question.strip().lower()}".encode()).hexdigest()[:32]

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
                ids=[SemanticCacheService._id(scope, question)], embeddings=[emb], documents=[question],
                metadatas=[{"tenant_id": tenant_id, "project_id": project_id, "scope": scope,
                            "answer": answer, "ts": time.time()}],
            )
        except Exception:  # noqa: BLE001 - cache write failure is non-fatal
            pass
