"""Knowledge: source ingestion (text/url) into Chroma, hybrid search, and Q&A pairs."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.knowledge.embeddings import resolve_embedder
from forge.knowledge.splitter import split_text
from forge.knowledge.store import ChromaStore, Hit
from forge.models import KbSource, Project, QaPair
from forge.secrets.store import SecretStore
from forge.util.http import shared_async_client


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", html)


class KnowledgeService:
    # --- embedder resolution (uses the project's provider key) ---
    @staticmethod
    async def embedder_for_project(session, tenant_id: str, project_id: str):
        proj = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
        cfg = (proj.config or {}) if proj else {}
        model = (cfg.get("rag_defaults") or {}).get("embedding_model", "openai:text-embedding-3-small")
        api_key = None
        ref = (cfg.get("provider_credentials") or {}).get("openai")
        if ref:
            try:
                val = await SecretStore().read_ref(tenant_id=tenant_id, project_id=project_id, ref=ref)
                api_key = val if isinstance(val, str) else (val.get("key") or val.get("value")) if isinstance(val, dict) else None
            except Exception:  # noqa: BLE001 - missing key -> offline embedder
                pass
        return resolve_embedder(model, api_key)

    @staticmethod
    def _store(embedder) -> ChromaStore:
        return ChromaStore(collection=f"forge_kb_{embedder.dim}")

    @staticmethod
    async def embedding_health(session, tenant_id: str, project_id: str) -> dict:
        """Detect the silent dim-mismatch trap: sources embedded with a different model
        (dim) than the project's CURRENT embedder won't be found in search. Surfaces a
        'needs re-embed' flag + the offending sources so the UI can warn + offer reingest."""
        from forge.knowledge.embeddings import _MODEL_DIMS

        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)

        def dim_of(model_name: str | None) -> int | None:
            if not model_name:
                return None
            if model_name.startswith("fake"):
                return 256
            return _MODEL_DIMS.get(model_name)

        sources = await KnowledgeService.list_sources(session, tenant_id, project_id)
        mismatched = [
            {"id": s.id, "name": s.name, "embedded_with": s.embedding_model, "dim": dim_of(s.embedding_model)}
            for s in sources
            if s.status == "ready" and dim_of(s.embedding_model) not in (None, embedder.dim)
        ]
        return {
            "current_model": embedder.name, "current_dim": embedder.dim,
            "sources": len(sources), "needs_reembed": bool(mismatched), "mismatched": mismatched,
        }

    @staticmethod
    def _qa_store(embedder) -> ChromaStore:
        # Q&A pairs live in their OWN dim-keyed collection so semantic match is a top-k
        # vector query (not an O(n) Python cosine over every row).
        return ChromaStore(collection=f"forge_qa_{embedder.dim}")

    @staticmethod
    def _qa_where(tenant_id: str, project_id: str, kinds: list[str] | None, kind: str = "any") -> dict:
        clauses: list[dict] = [{"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}}]
        wanted = [k for k in (kinds or []) if k] or ([kind] if kind and kind != "any" else [])
        if wanted:
            clauses.append({"kind": {"$in": wanted}})
        return {"$and": clauses}

    @staticmethod
    async def _ensure_qa_indexed(session, tenant_id, project_id, embedder) -> None:
        """Lazily backfill the Q&A vector collection from the DB rows (covers pre-existing
        pairs and embedder/dim switches — a new dim collection starts empty)."""
        rows = await KnowledgeService.list_qa(session, tenant_id, project_id)
        if not rows:
            return
        store = KnowledgeService._qa_store(embedder)
        where = {"$and": [{"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}}]}
        if store.count_where(where) >= len(rows):
            return
        questions = [r.question for r in rows]
        vectors = await embedder.aembed(questions)
        store.upsert(
            ids=[r.id for r in rows], embeddings=vectors, documents=questions,
            metadatas=[{"tenant_id": tenant_id, "project_id": project_id, "kind": r.kind, "answer": r.answer} for r in rows],
        )

    # --- sources ---
    @staticmethod
    async def list_sources(session: AsyncSession, tenant_id: str, project_id: str) -> list[KbSource]:
        rows = await session.execute(select(KbSource).where(KbSource.tenant_id == tenant_id, KbSource.project_id == project_id))
        return list(rows.scalars())

    @staticmethod
    async def create_source(session, tenant_id, project_id, *, kind, name, uri=None, text=None, folder="") -> KbSource:
        src = KbSource(tenant_id=tenant_id, project_id=project_id, kind=kind, name=name, uri=uri, folder=folder or "", status="queued", meta={"text": text} if text else {})
        session.add(src)
        await session.commit()
        await session.refresh(src)
        return src

    @staticmethod
    async def list_folders(session, tenant_id, project_id) -> list[str]:
        """Distinct non-empty folder names in this project's sources."""
        rows = await session.execute(
            select(KbSource.folder).where(KbSource.tenant_id == tenant_id, KbSource.project_id == project_id).distinct()
        )
        return sorted({f for (f,) in rows if f})

    @staticmethod
    async def _source_ids_for_folders(session, tenant_id, project_id, folders: list[str]) -> list[str]:
        rows = await session.execute(
            select(KbSource.id).where(
                KbSource.tenant_id == tenant_id, KbSource.project_id == project_id, KbSource.folder.in_(list(folders))
            )
        )
        return [i for (i,) in rows]

    @staticmethod
    async def ingest(session, src: KbSource) -> KbSource:
        src.status = "processing"
        await session.commit()
        try:
            if src.kind == "text":
                content = (src.meta or {}).get("text", "")
            elif src.kind == "url":
                from forge.util.ssrf import guarded_get
                r = await guarded_get(shared_async_client(), src.uri, timeout=20, follow_redirects=True)
                content = _strip_html(r.text)
            elif src.kind == "crawl":
                from forge.knowledge.crawl import crawl_site
                pages = await crawl_site(src.uri, int((src.meta or {}).get("max_pages", 10)))
                content = "\n\n".join(f"# {u}\n{t}" for u, t in pages.items())
                src.meta = {**(src.meta or {}), "pages_crawled": len(pages)}
            else:
                raise ValueError(f"Unsupported source kind for ingest: {src.kind}")

            chunks = split_text(content, 1000, 200)
            embedder = await KnowledgeService.embedder_for_project(session, src.tenant_id, src.project_id)
            store = KnowledgeService._store(embedder)
            if chunks:
                vectors = await embedder.aembed(chunks)
                store.upsert(
                    ids=[f"{src.id}:{i}" for i in range(len(chunks))],
                    embeddings=vectors,
                    documents=chunks,
                    metadatas=[{"tenant_id": src.tenant_id, "project_id": src.project_id, "source_id": src.id, "chunk_idx": i} for i in range(len(chunks))],
                )
            src.chunks = len(chunks)
            src.embedding_model = embedder.name
            src.status = "ready"
        except Exception as e:  # noqa: BLE001
            src.status = "error"
            src.meta = {**(src.meta or {}), "error": str(e)}
        await session.commit()
        await session.refresh(src)
        return src

    @staticmethod
    async def reingest(session, src: KbSource) -> KbSource:
        """Re-fetch + re-embed an existing source (after content changes upstream, or to
        re-embed under the project's current embedder). Clears the old vectors first."""
        try:
            embedder = await KnowledgeService.embedder_for_project(session, src.tenant_id, src.project_id)
            KnowledgeService._store(embedder).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
        except Exception:  # noqa: BLE001
            pass
        for collection in ("forge_kb", "forge_kb_256", "forge_kb_1536", "forge_kb_3072"):
            try:
                ChromaStore(collection=collection).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
            except Exception:  # noqa: BLE001
                pass
        return await KnowledgeService.ingest(session, src)

    @staticmethod
    async def delete_source(session, src: KbSource) -> None:
        # Vectors are stored in a dimension-keyed collection (forge_kb_<dim>), so the
        # delete MUST target that same collection — a bare ChromaStore() hits the
        # default `forge_kb` collection and silently leaves the real chunks behind
        # (they keep showing up in search). Resolve the project's embedder to get the
        # right collection; also sweep the other known collection as a safety net in
        # case the source was embedded under a different model/dim earlier.
        try:
            embedder = await KnowledgeService.embedder_for_project(session, src.tenant_id, src.project_id)
            KnowledgeService._store(embedder).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
        except Exception:  # noqa: BLE001
            pass
        for collection in ("forge_kb", "forge_kb_256", "forge_kb_1536", "forge_kb_3072"):
            try:
                ChromaStore(collection=collection).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
            except Exception:  # noqa: BLE001
                pass
        await session.delete(src)
        await session.commit()

    @staticmethod
    async def search(
        session, tenant_id, project_id, query, *, top_k=5, source_ids=None, folders=None,
        embedder=None, embedding=None,
    ) -> list[Hit]:
        """Vector search over the project's chunks.

        `embedder`/`embedding` let a caller that already embedded the query (e.g. the
        retrieval node, which reuses one vector for docs AND Q&A) skip re-embedding.
        `folders` narrows to sources in those folders (resolved to source ids here, so
        existing Chroma data needs no re-ingest).
        """
        if folders:
            folder_ids = await KnowledgeService._source_ids_for_folders(session, tenant_id, project_id, folders)
            source_ids = sorted(set(folder_ids) & set(source_ids)) if source_ids else folder_ids
            if not source_ids:
                return []
        embedder = embedder or await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        vec = embedding if embedding is not None else await embedder.aembed_query(query)
        return KnowledgeService._store(embedder).query(
            embedding=vec, tenant_id=tenant_id, project_id=project_id, top_k=top_k, source_ids=source_ids
        )

    # --- Q&A ---
    @staticmethod
    async def list_qa(session, tenant_id, project_id) -> list[QaPair]:
        rows = await session.execute(select(QaPair).where(QaPair.tenant_id == tenant_id, QaPair.project_id == project_id))
        return list(rows.scalars())

    @staticmethod
    async def list_qa_kinds(session, tenant_id, project_id) -> list[str]:
        """Distinct Q&A kinds/categories in this project (for filter chips + node dropdowns)."""
        rows = await session.execute(
            select(QaPair.kind).where(QaPair.tenant_id == tenant_id, QaPair.project_id == project_id).distinct()
        )
        return sorted({k for (k,) in rows if k})

    @staticmethod
    async def create_qa(session, tenant_id, project_id, *, question, answer, kind="faq", tags=None) -> QaPair:
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        emb = await embedder.aembed_query(question)
        qa = QaPair(tenant_id=tenant_id, project_id=project_id, question=question, answer=answer, kind=(kind or "faq").strip(), tags=tags or [], q_embedding=emb)
        session.add(qa)
        await session.commit()
        await session.refresh(qa)
        try:
            KnowledgeService._qa_store(embedder).upsert(
                ids=[qa.id], embeddings=[emb], documents=[question],
                metadatas=[{"tenant_id": tenant_id, "project_id": project_id, "kind": qa.kind, "answer": answer}],
            )
        except Exception:  # noqa: BLE001 - store unavailable; lazy reindex will backfill
            pass
        return qa

    @staticmethod
    async def delete_qa(session, qa: QaPair) -> None:
        for dim in (256, 1536, 3072):
            try:
                ChromaStore(collection=f"forge_qa_{dim}").delete_ids([qa.id])
            except Exception:  # noqa: BLE001
                pass
        await session.delete(qa)
        await session.commit()

    @staticmethod
    def _kind_filter(kinds: list[str] | None, kind: str = "any"):
        """Build a row predicate from either a kinds list (empty/None = all) or the
        legacy single `kind` ('any' = all)."""
        wanted = [k for k in (kinds or []) if k]
        if wanted:
            allowed = set(wanted)
            return lambda r: r.kind in allowed
        if kind and kind != "any":
            return lambda r: r.kind == kind
        return lambda r: True

    @staticmethod
    async def top_qa(
        session, tenant_id, project_id, query, *, top_k=3, threshold=0.3, kind="any", kinds=None,
        embedder=None, embedding=None,
    ) -> list[dict]:
        """Top-k semantically matching Q&A pairs above a (looser) threshold, deduped.

        Unlike `lookup` (a single hard deflection at ~0.85), this feeds a grounded agent
        the closest FAQs so paraphrased questions still get the project's answer.
        """
        embedder = embedder or await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        q = embedding if embedding is not None else await embedder.aembed_query(query)
        await KnowledgeService._ensure_qa_indexed(session, tenant_id, project_id, embedder)
        where = KnowledgeService._qa_where(tenant_id, project_id, kinds, kind)
        try:
            hits = KnowledgeService._qa_store(embedder).query_where(embedding=q, where=where, top_k=max(top_k * 2, top_k))
        except Exception:  # noqa: BLE001 - store empty / not ready
            return []
        seen: set = set()
        out: list[dict] = []
        for h in hits:
            if h.score < threshold:
                continue
            answer = (h.metadata or {}).get("answer", "")
            key = (h.text.strip().lower(), str(answer).strip().lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"question": h.text, "answer": answer, "score": round(h.score, 3), "kind": (h.metadata or {}).get("kind")})
            if len(out) >= top_k:
                break
        return out

    @staticmethod
    async def lookup(session, tenant_id, project_id, query, *, threshold=0.85, kind="any", kinds=None) -> dict | None:
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        q = await embedder.aembed_query(query)
        res = await KnowledgeService.top_qa(
            session, tenant_id, project_id, query, top_k=1, threshold=threshold,
            kind=kind, kinds=kinds, embedder=embedder, embedding=q,
        )
        return res[0] if res else None
