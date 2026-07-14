"""Knowledge: source ingestion (text/url) into Chroma, hybrid search, and Q&A pairs."""

from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.knowledge.embeddings import KNOWN_EMBEDDING_DIMS, cosine, resolve_embedder
from forge.knowledge.splitter import chunk_text
from forge.knowledge.store import ChromaStore, Hit, _where
from forge.models import KbSource, Project, QaPair
from forge.secrets.store import SecretStore
from forge.util.http import shared_async_client

log = logging.getLogger("forge.knowledge")

# Ingestion batching + retry (finding: all-or-nothing ingest with no backoff/checkpoint). Embeds
# are done in batches, each upserted immediately so partial progress is durable in the store, and
# retried with exponential backoff so a transient embedder/API blip doesn't fail the whole source.
_EMBED_BATCH_SIZE = 128
_EMBED_MAX_ATTEMPTS = 3
_EMBED_BACKOFF_BASE = 0.5

# Fallback chunking when neither the source's own meta nor the project's rag_defaults set
# one. Overridable per project (config.rag_defaults) or per source (re-chunk). The web UI
# mirrors these defaults in knowledge.tsx; the server stays authoritative for what's used.
_DEFAULT_CHUNK_STRATEGY = "recursive"
_DEFAULT_CHUNK_SIZE = 1000
_DEFAULT_CHUNK_OVERLAP = 200
# Parent-child mode: size (chars) of the small child chunks that actually get embedded. The
# parent window uses the normal chunk_size. Overridable via rag_defaults.child_chunk_size.
_DEFAULT_CHILD_CHUNK_SIZE = 300
# Chunk-map visualizer point budget: the UI lets the user choose how many chunks to plot; this
# is the default and the hard ceiling (projecting/SVD-ing every vector gets slow + heavy).
_CHUNK_MAP_DEFAULT_POINTS = 400
_CHUNK_MAP_MAX_POINTS = 2000


def _dim_collections(prefix: str) -> list[str]:
    """Every Chroma collection name a source/QA vector may live in: the bare (legacy,
    dimensionless) prefix plus one per known embedding dim. delete/reingest sweep all of
    them so an embedder/dim switch never orphans vectors (derived from the single source of
    truth KNOWN_EMBEDDING_DIMS, so this list can't drift from the model table again)."""
    return [prefix, *(f"{prefix}_{d}" for d in sorted(KNOWN_EMBEDDING_DIMS))]


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", html)


def _page_title(url: str) -> str:
    """A short, readable title for a crawled page derived from its URL path (last non-empty
    segment), falling back to the host. Stored in chunk metadata so retrieval can cite the page."""
    from urllib.parse import unquote, urlparse

    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    return unquote(segs[-1]) if segs else (p.netloc or url)


def _project_chunks_2d(embeddings: list, query_vec: list | None = None, *, spread: float = 1000.0):
    """PCA the chunk vectors down to 2-D for the chunk map: fit on the chunk embeddings, then
    project both the chunks AND (optionally) the query into the SAME plane so nearby dots really
    are semantically near. Both axes are scaled by one factor so distances stay proportional.
    Returns (coords: list[[x, y]], query_xy: [x, y] | None). Falls back to a plain grid layout
    when numpy is unavailable or the vectors are degenerate - the map still renders, just without
    the semantic placement."""
    n = len(embeddings)
    if n == 0:
        return [], None

    def _grid():
        import math

        cols = max(1, int(math.ceil(math.sqrt(n))))
        return [[float((i % cols) * 60), float((i // cols) * 60)] for i in range(n)], None

    try:
        import numpy as np

        # Chroma rows are already numpy-ish sequences; asarray copies them into one (n, d) matrix
        # directly (a ragged/degenerate set raises and drops to the grid fallback below).
        x = np.asarray(embeddings, dtype=float)
        if x.ndim != 2 or x.shape[0] < 2:
            return _grid()
        mean = x.mean(axis=0)
        xc = x - mean
        _, _, vt = np.linalg.svd(xc, full_matrices=False)
        k = min(2, vt.shape[0])
        comps = vt[:k]  # (k, d) principal directions
        proj = xc @ comps.T  # (n, k)
        if k == 1:  # only one usable component -> spread along x, flat y
            proj = np.column_stack([proj[:, 0], np.zeros(n)])
        mn = proj.min(axis=0)
        span = float((proj.max(axis=0) - mn).max()) or 1.0
        norm = (proj - mn) / span * spread
        coords = [[round(float(a), 2), round(float(b), 2)] for a, b in norm]
        query_xy = None
        if query_vec is not None:
            q = (np.asarray(query_vec, dtype=float) - mean) @ comps.T
            q = np.array([q[0], q[1] if k > 1 else 0.0])
            qn = (q - mn) / span * spread
            query_xy = [round(float(qn[0]), 2), round(float(qn[1]), 2)]
        return coords, query_xy
    except Exception:  # noqa: BLE001 - numpy missing / SVD failed -> grid layout
        return _grid()


class KnowledgeService:
    # --- embedder resolution (uses the project's provider key) ---
    @staticmethod
    async def embedder_for_project(session, tenant_id: str, project_id: str):
        proj = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
        cfg = (proj.config or {}) if proj else {}
        # None => resolve_embedder picks the local fastembed default (single source of truth
        # for the default lives in embeddings._DEFAULT_FASTEMBED, not duplicated here).
        model = (cfg.get("rag_defaults") or {}).get("embedding_model")
        api_key = None
        ref = (cfg.get("provider_credentials") or {}).get("openai")
        if ref:
            try:
                val = await SecretStore().read_ref(tenant_id=tenant_id, project_id=project_id, ref=ref)
                api_key = val if isinstance(val, str) else (val.get("key") or val.get("value")) if isinstance(val, dict) else None
            except Exception:  # noqa: BLE001 - missing key -> offline embedder
                pass
        # resolve_embedder constructs the embedder (fastembed's first-use path loads the ONNX
        # model, and downloads it when no baked cache exists) - CPU/IO-bound work that would
        # otherwise block the event loop (and every concurrent request) on a cold start. The
        # instance is cached inside resolve_embedder, so this only pays off-loop once per model.
        return await asyncio.to_thread(resolve_embedder, model, api_key)

    @staticmethod
    async def _rag_defaults(session, project_id: str) -> dict:
        """Project-level RAG knobs (embedding_model, chunk_size, chunk_overlap,
        chunking_strategy). Empty dict when the project has none configured."""
        proj = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
        cfg = (proj.config or {}) if proj else {}
        return cfg.get("rag_defaults") or {}

    @staticmethod
    def _store(embedder) -> ChromaStore:
        return ChromaStore(collection=f"forge_kb_{embedder.dim}")

    @staticmethod
    def _collapse_parents(hits: list[Hit]) -> list[Hit]:
        """Parent-child retrieval: a child hit carries `parent_id` + `parent_text` in metadata.
        Swap the small matched child for its wider parent window and keep only the best-scoring
        child per parent (hits arrive score-ordered), so the agent gets deduped, context-rich
        passages. Flat-mode hits (no `parent_id`) pass through unchanged, so this is a no-op
        until a project opts into parent_child ingestion."""
        from dataclasses import replace

        out: list[Hit] = []
        seen: set[str] = set()
        for h in hits:
            pid = (h.metadata or {}).get("parent_id")
            if not pid:
                out.append(h)
                continue
            if pid in seen:
                continue
            seen.add(pid)
            out.append(replace(h, text=(h.metadata or {}).get("parent_text") or h.text))
        return out

    @staticmethod
    async def embedding_health(session, tenant_id: str, project_id: str) -> dict:
        """Detect the silent model-mismatch trap: sources embedded with a different model than
        the project's CURRENT embedder. A DIFFERENT-dim model lands in a different collection
        (found nothing); a SAME-dim different model shares the collection and would return
        wrong/foreign chunks - search now isolates by model identity, so those also read as
        empty. Either way the fix is a re-embed, so flag any source whose embedding model NAME
        differs from the current one (not just a dim change)."""
        from forge.knowledge.embeddings import _MODEL_DIMS

        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)

        def dim_of(model_name: str | None) -> int | None:
            if not model_name:
                return None
            # Legacy: sources embedded by the removed hashed FakeEmbedder (dim 256). Kept
            # only so those pre-existing sources are flagged as needing a re-embed onto the
            # current model - the FakeEmbedder itself is gone.
            if model_name.startswith("fake"):
                return 256
            return _MODEL_DIMS.get(model_name)

        sources = await KnowledgeService.list_sources(session, tenant_id, project_id)
        mismatched = [
            {"id": s.id, "name": s.name, "embedded_with": s.embedding_model, "dim": dim_of(s.embedding_model),
             "same_dim": dim_of(s.embedding_model) == embedder.dim}
            for s in sources
            if s.status == "ready" and s.embedding_model and s.embedding_model != embedder.name
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
        pairs and embedder/dim switches - a new dim collection starts empty)."""
        rows = await KnowledgeService.list_qa(session, tenant_id, project_id)
        if not rows:
            return
        store = KnowledgeService._qa_store(embedder)
        where = {"$and": [{"tenant_id": {"$eq": tenant_id}}, {"project_id": {"$eq": project_id}}]}
        # Index only the rows actually MISSING from this dim-keyed collection. The old
        # `count_where(...) >= len(rows)` check wrongly skipped backfill whenever the counts
        # happened to match (a stale id present, or the pair never indexed under this dim),
        # which left pairs silently unfindable.
        existing = set(store.ids_where(where))
        missing = [r for r in rows if r.id not in existing]
        if not missing:
            return
        questions = [r.question for r in missing]
        vectors = await embedder.aembed(questions)
        store.upsert(
            ids=[r.id for r in missing], embeddings=vectors, documents=questions,
            metadatas=[{"tenant_id": tenant_id, "project_id": project_id, "kind": r.kind, "answer": r.answer} for r in missing],
        )

    # --- sources ---
    @staticmethod
    async def list_sources(session: AsyncSession, tenant_id: str, project_id: str) -> list[KbSource]:
        rows = await session.execute(select(KbSource).where(KbSource.tenant_id == tenant_id, KbSource.project_id == project_id))
        return list(rows.scalars())

    @staticmethod
    async def create_source(session, tenant_id, project_id, *, kind, name, uri=None, text=None, folder="", chunking_strategy=None, meta=None) -> KbSource:
        # `meta` carries source-kind knobs the ingest reads later - e.g. crawl {max_pages,
        # max_depth, crawl_delay} (finding: crawl had a hardcoded max_pages=10, no depth).
        src_meta: dict = dict(meta or {})
        if text:
            src_meta["text"] = text
        if chunking_strategy:
            src_meta["chunk_strategy"] = chunking_strategy
        src = KbSource(tenant_id=tenant_id, project_id=project_id, kind=kind, name=name, uri=uri, folder=folder or "", status="queued", meta=src_meta)
        session.add(src)
        await session.commit()
        await session.refresh(src)
        return src

    @staticmethod
    def _apply_chunk_overrides(src: KbSource, *, chunking_strategy=None, chunk_size=None, chunk_overlap=None) -> None:
        """Stash per-source chunking overrides into meta so the next ingest picks them up.
        None fields are left untouched (keep the source's current value)."""
        meta = dict(src.meta or {})
        if chunking_strategy:
            meta["chunk_strategy"] = chunking_strategy
        if chunk_size is not None:
            meta["chunk_size"] = int(chunk_size)
        if chunk_overlap is not None:
            meta["chunk_overlap"] = int(chunk_overlap)
        src.meta = meta

    @staticmethod
    async def rechunk(session, src: KbSource, *, chunking_strategy=None, chunk_size=None, chunk_overlap=None) -> KbSource:
        """Apply chunking overrides then re-ingest (re-split + re-embed). Text/file sources
        reuse their stored text; url/crawl are re-fetched."""
        KnowledgeService._apply_chunk_overrides(
            src, chunking_strategy=chunking_strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        await session.commit()
        return await KnowledgeService.reingest(session, src)

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
            # Build per-provenance SEGMENTS: (text, extra_metadata). A crawl keeps one segment
            # PER PAGE (so each chunk carries its own page URL/title instead of collapsing every
            # page into one document); every other kind is a single segment.
            segments: list[tuple[str, dict]] = []
            if src.kind in ("text", "file"):
                # File uploads stash their decoded text in meta["text"] (see the upload
                # endpoint), same as inline text sources - so they share this branch.
                segments = [((src.meta or {}).get("text", ""), {})]
            elif src.kind == "url":
                from forge.util.ssrf import guarded_get
                r = await guarded_get(shared_async_client(), src.uri, timeout=20, follow_redirects=True)
                segments = [(_strip_html(r.text), {})]
            elif src.kind == "crawl":
                from forge.knowledge.crawl import (
                    DEFAULT_DELAY_SECONDS,
                    DEFAULT_MAX_DEPTH,
                    DEFAULT_MAX_PAGES,
                    crawl_site,
                )
                m = src.meta or {}
                pages = await crawl_site(
                    src.uri, int(m.get("max_pages") or DEFAULT_MAX_PAGES),
                    max_depth=int(m["max_depth"]) if m.get("max_depth") is not None else DEFAULT_MAX_DEPTH,
                    delay=float(m["crawl_delay"]) if m.get("crawl_delay") is not None else DEFAULT_DELAY_SECONDS,
                )
                segments = [(t, {"page_url": u, "page_title": _page_title(u)}) for u, t in pages.items()]
                src.meta = {**m, "pages_crawled": len(pages)}
            else:
                raise ValueError(f"Unsupported source kind for ingest: {src.kind}")

            rag = await KnowledgeService._rag_defaults(session, src.project_id)
            meta = src.meta or {}
            # Per-source overrides (set by re-chunk) win over the project rag_defaults.
            strategy = meta.get("chunk_strategy") or rag.get("chunking_strategy") or _DEFAULT_CHUNK_STRATEGY
            chunk_size = int(meta.get("chunk_size") or rag.get("chunk_size") or _DEFAULT_CHUNK_SIZE)
            # Overlap must honor an explicit 0 (no overlap), so check for None rather than
            # falsiness before falling back to the project default.
            _overlap = meta.get("chunk_overlap")
            if _overlap is None:
                _overlap = rag.get("chunk_overlap")
            overlap = int(_overlap) if _overlap is not None else _DEFAULT_CHUNK_OVERLAP
            parent_child = rag.get("retrieval_mode") == "parent_child"
            child_size = int(rag.get("child_chunk_size") or _DEFAULT_CHILD_CHUNK_SIZE)

            embedder = await KnowledgeService.embedder_for_project(session, src.tenant_id, src.project_id)
            store = KnowledgeService._store(embedder)

            # Clamp chunk_size to the embedder's safe char budget: anything longer is SILENTLY
            # truncated by the model at embed time, losing the tail of every chunk. Record the
            # clamp so the UI/user can see why the effective size differs from what they asked.
            max_chars = getattr(embedder, "max_input_chars", None)
            chunk_clamped = None
            if max_chars and chunk_size > max_chars:
                chunk_clamped = chunk_size
                chunk_size = max_chars
                log.warning("ingest: chunk_size %d exceeds %s max input (%d chars); clamping to %d",
                            chunk_clamped, embedder.name, max_chars, chunk_size)
            if max_chars:
                child_size = min(child_size, max_chars)

            # Semantic chunking embeds each sentence, so it needs the embedder AND must run off
            # the event loop; every other strategy is pure-Python. This helper hides that.
            async def _split(t: str, size: int) -> list[str]:
                if strategy == "semantic":
                    return await asyncio.to_thread(
                        chunk_text, t, strategy=strategy, chunk_size=size, overlap=overlap,
                        embed_fn=embedder.embed,
                    )
                return chunk_text(t, strategy=strategy, chunk_size=size, overlap=overlap)

            # Source-level provenance on EVERY chunk so retrieval can cite it, plus the embedding
            # model identity so search can isolate chunks from a different (even same-dim) model.
            base = {"tenant_id": src.tenant_id, "project_id": src.project_id, "source_id": src.id,
                    "source_name": src.name or "", "source_uri": src.uri or "", "embedding_model": embedder.name}
            ids: list[str] = []
            docs: list[str] = []
            metas: list[dict] = []
            n_parents = 0
            child_overlap = min(overlap, max(child_size // 4, 0))
            for seg_text, seg_meta in segments:
                seg_base = {**base, **seg_meta}
                if parent_child:
                    # Split into parent windows (the chosen strategy), then each parent into small
                    # children (recursive). ONLY children are embedded/searched; each child carries
                    # its parent's text so retrieval hands back the wider context (see search()).
                    for parent in await _split(seg_text, chunk_size):
                        pj = n_parents
                        for child in chunk_text(parent, strategy="recursive", chunk_size=child_size, overlap=child_overlap):
                            idx = len(ids)
                            ids.append(f"{src.id}:{idx}")
                            docs.append(child)
                            metas.append({**seg_base, "chunk_idx": idx, "parent_idx": pj,
                                          "parent_id": f"{src.id}:p{pj}", "parent_text": parent})
                        n_parents += 1
                else:
                    for chunk in await _split(seg_text, chunk_size):
                        idx = len(ids)
                        ids.append(f"{src.id}:{idx}")
                        docs.append(chunk)
                        metas.append({**seg_base, "chunk_idx": idx})

            embedded = await KnowledgeService._embed_and_upsert(session, src, store, embedder, ids, docs, metas)
            src.chunks = embedded  # embedded/searchable units (children in parent_child mode)
            src.embedding_model = embedder.name
            # Record the chunking actually used (covers the project-default fallback) so the UI
            # can show it and reingest / re-chunk reuses it.
            src.meta = {**(src.meta or {}), "chunk_strategy": strategy, "chunk_size": chunk_size,
                        "chunk_overlap": overlap, "retrieval_mode": "parent_child" if parent_child else "chunk"}
            if chunk_clamped:
                src.meta["chunk_size_requested"] = chunk_clamped
            if parent_child:
                src.meta["parents"] = n_parents
            src.meta.pop("ingest_progress", None)  # completed cleanly; drop the resume marker
            src.status = "ready"
        except Exception as e:  # noqa: BLE001
            src.status = "error"
            src.meta = {**(src.meta or {}), "error": str(e)}
        await session.commit()
        await session.refresh(src)
        return src

    @staticmethod
    async def _embed_with_retry(embedder, docs: list[str]) -> list:
        """Embed one batch with exponential backoff so a transient embedder/API failure retries
        instead of failing the whole source. Raises after the last attempt (caller marks error)."""
        delay = _EMBED_BACKOFF_BASE
        for attempt in range(1, _EMBED_MAX_ATTEMPTS + 1):
            try:
                return await embedder.aembed(docs)
            except Exception:  # noqa: BLE001 - retry transient failures, re-raise the last one
                if attempt >= _EMBED_MAX_ATTEMPTS:
                    raise
                log.warning("embed batch failed (attempt %d/%d); retrying in %.1fs",
                            attempt, _EMBED_MAX_ATTEMPTS, delay, exc_info=True)
                await asyncio.sleep(delay)
                delay *= 2
        return []  # unreachable (loop either returns or raises)

    @staticmethod
    async def _embed_and_upsert(session, src, store, embedder, ids, docs, metas) -> int:
        """Embed + upsert in batches, CHECKPOINTING progress after each batch. Each batch is
        upserted immediately, so a mid-way failure leaves the completed batches durable in the
        store (partial, not all-or-nothing) and `src.chunks`/`ingest_progress` reflect reality.
        Returns the number of chunks embedded."""
        total = len(ids)
        done = 0
        for start in range(0, total, _EMBED_BATCH_SIZE):
            b_ids, b_docs, b_metas = ids[start:start + _EMBED_BATCH_SIZE], docs[start:start + _EMBED_BATCH_SIZE], metas[start:start + _EMBED_BATCH_SIZE]
            vectors = await KnowledgeService._embed_with_retry(embedder, b_docs)
            store.upsert(ids=b_ids, embeddings=vectors, documents=b_docs, metadatas=b_metas)
            done += len(b_ids)
            if total > _EMBED_BATCH_SIZE:  # only checkpoint for multi-batch sources (avoid churn)
                src.chunks = done
                src.meta = {**(src.meta or {}), "ingest_progress": {"done": done, "total": total}}
                await session.commit()
        return done

    @staticmethod
    async def mark_error(session, src: KbSource, message: str) -> None:
        """Fail a source loudly (status=error + message in meta) instead of leaving it stuck
        in 'queued'/'processing' - e.g. when the background ingest task can't be scheduled."""
        src.status = "error"
        src.meta = {**(src.meta or {}), "error": message}
        await session.commit()
        await session.refresh(src)

    @staticmethod
    async def reingest(session, src: KbSource) -> KbSource:
        """Re-fetch + re-embed an existing source (after content changes upstream, or to
        re-embed under the project's current embedder). Clears the old vectors first."""
        try:
            embedder = await KnowledgeService.embedder_for_project(session, src.tenant_id, src.project_id)
            KnowledgeService._store(embedder).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
        except Exception:  # noqa: BLE001
            pass
        for collection in _dim_collections("forge_kb"):
            try:
                ChromaStore(collection=collection).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
            except Exception:  # noqa: BLE001
                pass
        return await KnowledgeService.ingest(session, src)

    @staticmethod
    async def run_ingest_bg(tenant_id: str, source_id: str, *, reingest: bool = False) -> None:
        """Ingest (or re-ingest) a source in its OWN DB session - the entrypoint for the
        fire-and-forget background task. Embedding a real model takes seconds to tens of
        seconds for a large doc, which would otherwise time out the upload HTTP request;
        the endpoint returns immediately and the UI polls the source's status.
        """
        from forge.db.base import SessionLocal

        async with SessionLocal() as s:
            src = (await s.execute(
                select(KbSource).where(KbSource.tenant_id == tenant_id, KbSource.id == source_id)
            )).scalar_one_or_none()
            if src is None:
                return
            if reingest:
                await KnowledgeService.reingest(s, src)
            else:
                await KnowledgeService.ingest(s, src)

    @staticmethod
    async def delete_source(session, src: KbSource) -> None:
        # Vectors are stored in a dimension-keyed collection (forge_kb_<dim>), so the
        # delete MUST target that same collection - a bare ChromaStore() hits the
        # default `forge_kb` collection and silently leaves the real chunks behind
        # (they keep showing up in search). Resolve the project's embedder to get the
        # right collection; also sweep the other known collection as a safety net in
        # case the source was embedded under a different model/dim earlier.
        try:
            embedder = await KnowledgeService.embedder_for_project(session, src.tenant_id, src.project_id)
            KnowledgeService._store(embedder).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
        except Exception:  # noqa: BLE001
            pass
        for collection in _dim_collections("forge_kb"):
            try:
                ChromaStore(collection=collection).delete_by_source(src.id, tenant_id=src.tenant_id, project_id=src.project_id)
            except Exception:  # noqa: BLE001
                pass
        await session.delete(src)
        await session.commit()

    @staticmethod
    async def dedupe_chunks(session, tenant_id, project_id) -> dict:
        """Remove EXACT-duplicate chunks (identical text, ignoring only surrounding whitespace)
        across the whole project, keeping the FIRST occurrence of each. Recomputes the chunk
        count of every source it touched so the UI stays accurate.

        Cleanup only: re-ingesting a source regenerates all its chunks, so if the duplicates come
        from the same document ingested twice, delete the duplicate SOURCE (Files tab) - otherwise
        the dupes reappear on the next reingest. Operates on the project's current embedder
        collection (the one search uses)."""
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        store = KnowledgeService._store(embedder)
        where = _where(tenant_id, project_id, None)
        data = store.list_docs(where)
        ids, docs, metas = data["ids"], data["documents"], data["metadatas"]

        seen: set[str] = set()
        dupe_ids: list[str] = []
        dupe_keys: set[str] = set()
        affected: set[str] = set()
        for i, cid in enumerate(ids):
            key = ((docs[i] if i < len(docs) else "") or "").strip()
            if not key:
                continue
            if key in seen:
                dupe_ids.append(cid)
                dupe_keys.add(key)
                sid = (metas[i] or {}).get("source_id") if i < len(metas) else None
                if sid:
                    affected.add(sid)
            else:
                seen.add(key)

        if dupe_ids:
            store.delete_ids(dupe_ids)
            # Keep each affected source's DB chunk count in sync with what actually remains.
            for sid in affected:
                remaining = store.count_where(_where(tenant_id, project_id, [sid]))
                src = (await session.execute(
                    select(KbSource).where(
                        KbSource.tenant_id == tenant_id, KbSource.project_id == project_id, KbSource.id == sid
                    )
                )).scalar_one_or_none()
                if src is not None:
                    src.chunks = remaining
            await session.commit()

        return {
            "removed": len(dupe_ids),
            "groups": len(dupe_keys),
            "sources_affected": len(affected),
            "remaining": len(ids) - len(dupe_ids),
        }

    @staticmethod
    async def search(
        session, tenant_id, project_id, query, *, top_k=5, source_ids=None, folders=None,
        embedder=None, embedding=None, hybrid=False, rerank=False, rerank_top_n=None,
        mmr=False, mmr_lambda=0.5,
    ) -> list[Hit]:
        """Vector search over the project's chunks - or hybrid (BM25 lexical + vector,
        fused via RRF) when `hybrid=True`. Hybrid is opt-in; vector-only is the default.

        When `rerank=True`, a second stage runs a local cross-encoder over a larger stage-1
        shortlist (`rerank_top_n`, default max(top_k*5, 25)) and keeps the best `top_k` - a
        big accuracy win at the cost of some latency. Opt-in; degrades to stage-1 order if the
        reranker model is unavailable (see knowledge/rerank.py). NOTE: a reranked `Hit.score` is a
        cross-encoder relevance on a DIFFERENT scale than cosine/fusion - a caller applying a
        cosine-tuned floor (e.g. the retrieval node's min_score) must not apply it to reranked
        hits (see nodes/rag.py). In hybrid mode `Hit.score` is the fused rank and `Hit.vector_score`
        the underlying cosine, so a cosine floor thresholds the latter.

        When `mmr=True`, a final Maximal Marginal Relevance pass re-ranks the candidate pool to
        trade a little relevance for diversity (`mmr_lambda` in [0,1]: 1.0 = pure relevance,
        lower = more diverse), reducing near-duplicate passages in the returned top_k.

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
        store = KnowledgeService._store(embedder)
        # Stage 1: pull a candidate shortlist. Over-fetch beyond top_k so parent-window collapse
        # (which dedups many child hits into a single parent) still yields top_k distinct
        # passages. The over-fetch is driven by the hits' own parent_id metadata, NOT the
        # project's live retrieval_mode, so it stays correct even when the mode was flipped after
        # ingest or sources are mixed. For flat data collapse is a no-op and the extra candidates
        # are simply sliced back off, leaving ordering identical to a plain top_k query. Reranking
        # needs its own (possibly larger) shortlist to trim down from. pool is always >= top_k.
        pool = max(rerank_top_n or top_k * 5, 25) if rerank else top_k
        pool = max(pool, top_k * 6)
        if hybrid:
            # Hybrid does a corpus scan + BM25 (CPU-bound); run it off the event loop so it
            # doesn't stall the SSE stream / other requests. The BM25 index itself is cached
            # per corpus version inside the store, so repeat queries skip the rebuild.
            hits = await asyncio.to_thread(
                store.hybrid_query, embedding=vec, query=query, tenant_id=tenant_id,
                project_id=project_id, top_k=pool, source_ids=source_ids,
            )
        else:
            hits = store.query(
                embedding=vec, tenant_id=tenant_id, project_id=project_id, top_k=pool, source_ids=source_ids
            )
        # Isolate the CURRENT embedding model: a same-dim different model shares the collection,
        # so drop any chunk tagged with a different model (legacy chunks with no tag are kept -
        # they predate the tagging and embedding_health flags them for re-embed separately).
        hits = [h for h in hits if (h.metadata or {}).get("embedding_model") in (None, embedder.name)]
        # Stage 2: optional cross-encoder rerank (over child text in parent_child mode). Only this
        # path needs the project config (reranker model), so the common flat/vector-only search
        # makes NO extra Project DB round-trip.
        if rerank:
            from forge.knowledge.rerank import arerank_hits

            rag = await KnowledgeService._rag_defaults(session, project_id)
            hits = await arerank_hits(query, hits, top_k=pool, model=rag.get("reranker_model"))
        # ...then collapse child hits to their (deduped) parent windows.
        collapsed = KnowledgeService._collapse_parents(hits)
        if mmr and len(collapsed) > top_k:
            collapsed = await KnowledgeService._mmr_rerank(store, tenant_id, project_id, source_ids, vec, collapsed, top_k, mmr_lambda)
        return collapsed[:top_k]

    @staticmethod
    async def _mmr_rerank(store, tenant_id, project_id, source_ids, query_vec, hits, top_k, lam):
        """Maximal Marginal Relevance: greedily pick hits that are relevant to the query yet
        dissimilar from those already picked, so the top_k isn't several near-duplicate chunks.
        Needs each hit's embedding (fetched by id); degrades to the input order (relevance-only)
        if embeddings can't be fetched, so it never breaks a search."""
        lam = min(max(float(lam), 0.0), 1.0)
        try:
            ids = [h.id for h in hits]
            dumped = await asyncio.to_thread(store.dump, _where(tenant_id, project_id, source_ids), ids=ids)
            emb_by_id = {i: list(e) for i, e in zip(dumped["ids"], dumped["embeddings"], strict=False) if e is not None}
        except Exception:  # noqa: BLE001 - can't fetch vectors -> relevance-only order
            return hits
        if not emb_by_id:
            return hits
        candidates = list(hits)
        selected: list[Hit] = []
        while candidates and len(selected) < top_k:
            best, best_score = None, float("-inf")
            for c in candidates:
                emb = emb_by_id.get(c.id)
                rel = cosine(query_vec, emb) if emb else float(c.score)
                div = max((cosine(emb, emb_by_id[s.id]) for s in selected if emb and s.id in emb_by_id), default=0.0)
                score = lam * rel - (1.0 - lam) * div
                if score > best_score:
                    best, best_score = c, score
            selected.append(best)
            candidates.remove(best)
        return selected

    @staticmethod
    async def chunk_map(
        session, tenant_id, project_id, *, query=None, folders=None, source_ids=None,
        limit=400, hybrid=False, rerank=False, top_k=8,
    ) -> dict:
        """Project the project's stored chunk vectors to 2-D (PCA) for the chunk-map visualizer.

        Each point is a chunk (a child chunk in parent_child mode) with its source, a short text
        preview, and its `parent_id` (so the UI can draw parent->child links). The full chunk
        text is fetched on demand per selection (see `chunk_detail`) to keep this payload lean
        even at large point budgets. When `query` is
        given, it is embedded, projected into the SAME plane (`query_point`), and the chunks that
        retrieval would return are tagged with their `retrieved` rank (so the UI can draw
        query->hit links). `limit` caps how many points are projected/returned (the user picks
        this in the UI); it is clamped to [1, _CHUNK_MAP_MAX_POINTS] so a huge value can't force
        an unbounded fetch + SVD. `truncated` says whether more chunks exist than were shown.
        Read-only: it never writes to the store.
        """
        limit = max(1, min(int(limit or _CHUNK_MAP_DEFAULT_POINTS), _CHUNK_MAP_MAX_POINTS))
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        if folders:
            folder_ids = await KnowledgeService._source_ids_for_folders(session, tenant_id, project_id, folders)
            source_ids = sorted(set(folder_ids) & set(source_ids)) if source_ids else folder_ids
            if not source_ids:
                return {"points": [], "sources": [], "query_point": None, "query": query or None, "total": 0, "truncated": False}

        where = _where(tenant_id, project_id, list(source_ids) if source_ids else None)

        store = KnowledgeService._store(embedder)
        dumped = store.dump(where, limit=limit)
        ids = list(dumped["ids"])
        docs = list(dumped["documents"])
        metas = list(dumped["metadatas"])
        embs = list(dumped["embeddings"])
        # `total` is the full corpus count. Derive it from the dump when the dump didn't hit the
        # cap (the dump already returned everything), and only pay a count scan when we truncated.
        n_sampled = len(ids)
        total = n_sampled if n_sampled < limit else store.count_where(where)
        if not ids:
            return {"points": [], "sources": [], "query_point": None, "query": query or None, "total": total, "truncated": False}

        qvec = await embedder.aembed_query(query) if query else None

        rank_by_id: dict = {}
        if query:
            # Run the overlay search FIRST so we know which chunks retrieval surfaces (reuse the
            # already-embedded query vector).
            hits = await KnowledgeService.search(
                session, tenant_id, project_id, query, top_k=top_k, source_ids=source_ids,
                embedder=embedder, embedding=qvec, hybrid=hybrid, rerank=rerank,
            )
            rank_by_id = {h.id: r + 1 for r, h in enumerate(hits)}
            # Pull in any retrieved chunk that fell OUTSIDE the sampled window, so a hit is never
            # silently missing from the map (and it lands on the same PCA plane as everything else).
            have = set(ids)
            missing = [hid for hid in rank_by_id if hid not in have]
            if missing:
                extra = store.dump(where, ids=missing)
                ids += list(extra["ids"])
                docs += list(extra["documents"])
                metas += list(extra["metadatas"])
                embs += list(extra["embeddings"])

        coords, query_xy = _project_chunks_2d(embs, qvec)

        points: list[dict] = []
        for i, cid in enumerate(ids):
            m = metas[i] if i < len(metas) else {}
            m = m or {}
            xy = coords[i] if i < len(coords) else [0.0, 0.0]
            p = {
                "id": cid, "x": xy[0], "y": xy[1],
                "source_id": m.get("source_id"), "chunk_idx": m.get("chunk_idx"),
                "parent_id": m.get("parent_id"),
                # Short preview only (hover tooltip + instant panel text). The panel swaps in the
                # full chunk via chunk_detail on select, so this stays small across all points.
                "preview": ((docs[i] if i < len(docs) else "") or "")[:180],
            }
            if cid in rank_by_id:
                p["retrieved"] = rank_by_id[cid]
            points.append(p)

        present = {p["source_id"] for p in points if p["source_id"]}
        srcs = await KnowledgeService.list_sources(session, tenant_id, project_id)
        sources = [{"id": s.id, "name": s.name} for s in srcs if s.id in present]

        return {
            "points": points, "sources": sources, "query_point": query_xy,
            "query": query or None, "total": total, "truncated": total > len(points),
        }

    @staticmethod
    async def chunk_detail(session, tenant_id, project_id, chunk_id: str) -> dict | None:
        """Full text (+ light metadata) of ONE stored chunk, scoped to the tenant/project so a
        caller-supplied id can't read another project's data. Backs the chunk-map detail panel:
        the map payload carries only a short preview, and the whole chunk is fetched here when a
        dot is selected. Returns None when the id isn't in this project's current-embedder
        collection. Read-only."""
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        store = KnowledgeService._store(embedder)
        got = store.get_texts([chunk_id], _where(tenant_id, project_id, None))
        ids = got["ids"]
        if not ids:
            return None
        docs, metas = got["documents"], got["metadatas"]
        m = (metas[0] if metas else {}) or {}
        return {
            "id": ids[0],
            "text": (docs[0] if docs else "") or "",
            "source_id": m.get("source_id"),
            "chunk_idx": m.get("chunk_idx"),
            "parent_id": m.get("parent_id"),
        }

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
            log.warning(
                "create_qa: failed to index Q&A pair %s into the vector store now; "
                "lazy backfill will retry on next lookup", qa.id, exc_info=True,
            )
        return qa

    @staticmethod
    async def delete_qa(session, qa: QaPair) -> None:
        # Sweep EVERY dim-keyed Q&A collection (not a hardcoded subset) - the default embedder
        # is 384-dim, so a subset that omitted 384 left the vector behind and the deleted FAQ
        # kept deflecting. _dim_collections is derived from KNOWN_EMBEDDING_DIMS so it can't
        # drift from the model table.
        for collection in _dim_collections("forge_qa"):
            try:
                ChromaStore(collection=collection).delete_ids([qa.id])
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
        where = KnowledgeService._qa_where(tenant_id, project_id, kinds, kind)
        try:
            # Backfill INSIDE the guard: a failed (re)index must degrade to "no matches"
            # rather than abort the lookup and surface as a hard error upstream.
            await KnowledgeService._ensure_qa_indexed(session, tenant_id, project_id, embedder)
            hits = KnowledgeService._qa_store(embedder).query_where(embedding=q, where=where, top_k=max(top_k * 2, top_k))
        except Exception:  # noqa: BLE001 - store empty / not ready, or backfill failed
            log.warning("top_qa: Q&A retrieval failed; returning no matches", exc_info=True)
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
        """Single best Q&A match at/above `threshold` (default a HARD 0.85 = near-verbatim). The
        primitive behind `deflect`; `top_qa` is the looser, advisory multi-hit path."""
        embedder = await KnowledgeService.embedder_for_project(session, tenant_id, project_id)
        q = await embedder.aembed_query(query)
        res = await KnowledgeService.top_qa(
            session, tenant_id, project_id, query, top_k=1, threshold=threshold,
            kind=kind, kinds=kinds, embedder=embedder, embedding=q,
        )
        return res[0] if res else None

    @staticmethod
    async def deflect(session, tenant_id, project_id, query, *, threshold=0.85, kinds=None) -> dict | None:
        """High-confidence VERBATIM Q&A deflection for a channel/assistant to answer directly.

        Returns the curated {question, answer, score, kind} ONLY when a Q&A question matches the
        user's question very closely (>= `threshold`), so the approved answer can be returned
        verbatim (skipping the LLM); None below the bar, so the normal grounded/advisory path
        runs. This is the intended production entrypoint for the otherwise-unused high-threshold
        deflection - unlike `top_qa`, which merely feeds an agent the closest FAQs as grounding.

        NOTE: not yet wired into the channels/assistant reply paths (out of scope for this
        change - those modules were not editable); until then Q&A remains advisory. Wiring a
        caller here is a one-liner: `if (hit := await KnowledgeService.deflect(...)): reply(hit["answer"])`.
        """
        return await KnowledgeService.lookup(session, tenant_id, project_id, query, threshold=threshold, kinds=kinds)
