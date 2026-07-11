"""Knowledge endpoints - sources (ingest text/url), Q&A pairs, and a search debugger."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.schemas.dto import (
    KbSourceCreate,
    KbSourceOut,
    KnowledgeMapIn,
    KnowledgeSearchIn,
    QaPairCreate,
    QaPairOut,
    RechunkBulkIn,
    RechunkIn,
)
from forge.services.knowledge import KnowledgeService
from forge.util.tasks import spawn

router = APIRouter(prefix="/v1/projects/{project_id}/knowledge", tags=["knowledge"])
qa_router = APIRouter(prefix="/v1/projects/{project_id}/qa-pairs", tags=["knowledge"])

# Shown on a source when the background ingest task can't be scheduled (in-flight ceiling
# reached). Better to fail the source visibly than leave it stuck "queued" forever.
_QUEUE_FULL_MSG = "ingest queue is full; please retry in a moment"


async def _queue_ingest(session, tenant_id: str, src, *, reingest: bool = False, label: str = "ingest") -> None:
    """Spawn the background ingest for `src`; if the task ceiling rejects it, mark the source
    errored (spawn returns False and closes the coro) so it doesn't sit 'queued' indefinitely."""
    if not spawn(KnowledgeService.run_ingest_bg(tenant_id, src.id, reingest=reingest), name=f"{label}:{src.id}"):
        await KnowledgeService.mark_error(session, src, _QUEUE_FULL_MSG)


@router.get("/sources", response_model=list[KbSourceOut])
async def list_sources(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.list_sources(session, tenant_id, project_id)


@router.post("/sources", response_model=KbSourceOut, status_code=201)
async def add_source(project_id: str, body: KbSourceCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    src = await KnowledgeService.create_source(session, tenant_id, project_id, kind=body.kind, name=body.name, uri=body.uri, text=body.text, folder=body.folder, chunking_strategy=body.chunking_strategy)
    # Ingest (fetch/chunk/embed) off the request: a real embedder takes seconds+ for a large
    # source, which would time out the HTTP call. The source returns as "queued"; the UI polls.
    await _queue_ingest(session, tenant_id, src)
    return src


@router.post("/sources/upload", response_model=KbSourceOut, status_code=201)
async def upload_source(
    project_id: str,
    file: UploadFile = File(...),
    folder: str = Form(""),
    chunking_strategy: str = Form(""),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    """Upload a document file (.txt/.md/.pdf and other text formats) into the knowledge base."""
    raw = await file.read()
    name = file.filename or "upload"
    lower = name.lower()
    if lower.endswith(".pdf"):
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Could not read PDF: {e}") from e
        if not text:
            raise HTTPException(422, "PDF contains no extractable text (scanned image PDFs need OCR).")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        if not text.strip():
            raise HTTPException(422, "File is empty or not a readable text format.")
    src = await KnowledgeService.create_source(session, tenant_id, project_id, kind="file", name=name, text=text, folder=folder, chunking_strategy=(chunking_strategy or None))
    # The file bytes are fully read above; only the chunk+embed runs in the background so a
    # large upload doesn't block (and time out) the request. Returns "queued"; the UI polls.
    await _queue_ingest(session, tenant_id, src)
    return src


@router.get("/folders", response_model=list[str])
async def list_folders(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.list_folders(session, tenant_id, project_id)


@router.get("/health")
async def embedding_health(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Embedding-dimension health: flags sources embedded with a different model than the
    project's current embedder (which would silently return no search results)."""
    return await KnowledgeService.embedding_health(session, tenant_id, project_id)


@router.post("/sources/{source_id}/reingest")
async def reingest_source(project_id: str, source_id: str, body: RechunkIn | None = None,
                          session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id)):
    """Re-fetch + re-embed a source (re-crawl a site, or re-embed under the current model).
    An optional body overrides the chunking (strategy / size / overlap) before re-ingest."""
    from sqlalchemy import select

    from forge.models import KbSource
    src = (await session.execute(select(KbSource).where(KbSource.tenant_id == tenant_id, KbSource.id == source_id))).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="source not found")
    if body and (body.chunking_strategy or body.chunk_size is not None or body.chunk_overlap is not None):
        KnowledgeService._apply_chunk_overrides(
            src, chunking_strategy=body.chunking_strategy,
            chunk_size=body.chunk_size, chunk_overlap=body.chunk_overlap,
        )
    # Mark pending + persist overrides, then re-embed off the request (see add_source).
    src.status = "queued"
    await session.commit()
    await _queue_ingest(session, tenant_id, src, reingest=True, label="reingest")
    return {"id": src.id, "status": src.status, "chunks": src.chunks}


@router.post("/sources/rechunk")
async def rechunk_sources(project_id: str, body: RechunkBulkIn, session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id)):
    """Re-chunk a multi-selected set of sources with one shared set of overrides
    (strategy / size / overlap), then re-embed each (in the background). Tenant/project-scoped."""
    from sqlalchemy import select

    from forge.models import KbSource
    if not body.source_ids:
        return []
    rows = (await session.execute(
        select(KbSource).where(
            KbSource.tenant_id == tenant_id, KbSource.project_id == project_id,
            KbSource.id.in_(body.source_ids),
        )
    )).scalars().all()
    for src in rows:
        KnowledgeService._apply_chunk_overrides(
            src, chunking_strategy=body.chunking_strategy,
            chunk_size=body.chunk_size, chunk_overlap=body.chunk_overlap,
        )
        src.status = "queued"
    await session.commit()  # persist overrides + queued status for the whole batch at once
    for src in rows:
        await _queue_ingest(session, tenant_id, src, reingest=True, label="rechunk")
    return [{"id": src.id, "status": src.status, "chunks": src.chunks} for src in rows]


@router.patch("/sources/{source_id}", response_model=KbSourceOut)
async def update_source(project_id: str, source_id: str, body: dict, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Move a source between folders (the only mutable field; content requires re-ingest)."""
    from sqlalchemy import select

    from forge.models import KbSource
    src = (await session.execute(select(KbSource).where(KbSource.tenant_id == tenant_id, KbSource.id == source_id))).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "Source not found")
    if "folder" in body:
        src.folder = str(body["folder"] or "")
    await session.commit()
    await session.refresh(src)
    return src


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(project_id: str, source_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    from sqlalchemy import select

    from forge.models import KbSource
    src = (await session.execute(select(KbSource).where(KbSource.tenant_id == tenant_id, KbSource.id == source_id))).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "Source not found")
    await KnowledgeService.delete_source(session, src)


@router.post("/search")
async def search(project_id: str, body: KnowledgeSearchIn, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    hits = await KnowledgeService.search(session, tenant_id, project_id, body.query, top_k=body.top_k, folders=body.folders, hybrid=body.hybrid, rerank=body.rerank)
    return [{"text": h.text, "score": round(h.score, 4), "source_id": h.metadata.get("source_id")} for h in hits]


@router.post("/dedupe")
async def dedupe_chunks(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Remove exact-duplicate chunks (identical text) project-wide, keeping one copy of each.
    Returns {removed, groups, sources_affected, remaining}."""
    return await KnowledgeService.dedupe_chunks(session, tenant_id, project_id)


@router.post("/map")
async def chunk_map(project_id: str, body: KnowledgeMapIn, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Chunk-map visualizer: 2-D (PCA) projection of the project's chunk vectors, colored by
    source, with an optional query overlay showing what retrieval returns. Read-only."""
    return await KnowledgeService.chunk_map(
        session, tenant_id, project_id, query=body.query, folders=body.folders,
        source_ids=body.source_ids, limit=body.limit, hybrid=body.hybrid,
        rerank=body.rerank, top_k=body.top_k,
    )


@router.get("/chunk")
async def chunk_detail(project_id: str, chunk_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Full text (+ light metadata) of a single chunk, fetched on demand when a dot is selected
    in the chunk map (the map payload carries only a short preview). Scoped to the tenant/project.
    404 when the id isn't in this project's current-embedder collection."""
    detail = await KnowledgeService.chunk_detail(session, tenant_id, project_id, chunk_id)
    if detail is None:
        raise HTTPException(404, "Chunk not found.")
    return detail


@qa_router.get("", response_model=list[QaPairOut])
async def list_qa(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.list_qa(session, tenant_id, project_id)


@qa_router.get("/kinds", response_model=list[str])
async def list_qa_kinds(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.list_qa_kinds(session, tenant_id, project_id)


@qa_router.post("", response_model=QaPairOut, status_code=201)
async def add_qa(project_id: str, body: QaPairCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.create_qa(session, tenant_id, project_id, question=body.question, answer=body.answer, kind=body.kind, tags=body.tags)


@qa_router.delete("/{qa_id}", status_code=204)
async def delete_qa(project_id: str, qa_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    from sqlalchemy import select

    from forge.models import QaPair
    qa = (await session.execute(select(QaPair).where(QaPair.tenant_id == tenant_id, QaPair.id == qa_id))).scalar_one_or_none()
    if qa is None:
        raise HTTPException(404, "Q&A pair not found")
    await KnowledgeService.delete_qa(session, qa)
