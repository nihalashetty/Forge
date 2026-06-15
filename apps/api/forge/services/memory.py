"""MemoryService — persistent long-term memory with semantic recall.

Unlike the per-thread checkpointer (which holds one conversation), memories persist
across threads/channels and are recalled by meaning. Stored in a `memories` row + a
dim-keyed vector collection (`forge_mem_<dim>`); scoped per project (+ optional `scope`
key for per-user/per-conversation memory passed from runtime context).
"""

from __future__ import annotations

from sqlalchemy import select

from forge.knowledge.store import ChromaStore
from forge.models import Memory
from forge.services.knowledge import KnowledgeService


class MemoryService:
    @staticmethod
    def _store(embedder) -> ChromaStore:
        return ChromaStore(collection=f"forge_mem_{embedder.dim}")

    @staticmethod
    async def remember(session, tenant_id: str, project_id: str, text: str, *, scope: str = "default", kind: str = "note") -> Memory:
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        emb = await embedder.aembed_query(text)
        m = Memory(tenant_id=tenant_id, project_id=project_id, scope=scope or "default", text=text, kind=kind)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        try:
            MemoryService._store(embedder).upsert(
                ids=[m.id], embeddings=[emb], documents=[text],
                metadatas=[{"tenant_id": tenant_id, "project_id": project_id, "scope": m.scope}],
            )
        except Exception:  # noqa: BLE001 - store unavailable; row still persists
            pass
        return m

    @staticmethod
    async def recall(session, tenant_id: str, project_id: str, query: str, *, scope: str = "default", top_k: int = 5) -> list[str]:
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        emb = await embedder.aembed_query(query)
        where = {"$and": [
            {"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}},
            {"scope": {"$eq": scope or "default"}},
        ]}
        try:
            hits = MemoryService._store(embedder).query_where(embedding=emb, where=where, top_k=top_k)
        except Exception:  # noqa: BLE001
            return []
        return [h.text for h in hits if h.text]

    @staticmethod
    async def list(session, tenant_id: str, project_id: str, *, scope: str | None = None) -> list[Memory]:
        q = select(Memory).where(Memory.tenant_id == tenant_id, Memory.project_id == project_id)
        if scope:
            q = q.where(Memory.scope == scope)
        return list((await session.execute(q.order_by(Memory.created_at.desc()))).scalars())
