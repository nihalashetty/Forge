"""Knowledge endpoints - sources (ingest text/url), Q&A pairs, and a search debugger."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_session, require_role
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
from forge.services.knowledge import KnowledgeService, _strip_html
from forge.util.tasks import spawn

router = APIRouter(prefix="/v1/projects/{project_id}/knowledge", tags=["knowledge"])
qa_router = APIRouter(prefix="/v1/projects/{project_id}/qa-pairs", tags=["knowledge"])

# Shown on a source when the background ingest task can't be scheduled (in-flight ceiling
# reached). Better to fail the source visibly than leave it stuck "queued" forever.
_QUEUE_FULL_MSG = "ingest queue is full; please retry in a moment"

# Upload DoS bounds (audit M3): cap the in-memory read and the PDF page count so a huge file or a
# small decompression-bomb PDF can't exhaust the process. A global request-body-size limit is best
# enforced at the reverse proxy / uvicorn in front of the app.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_MAX_PDF_PAGES = 500

# Extensions we KNOW are binary containers - decoding them as text yields mojibake garbage that
# then gets embedded, so reject with a clear message instead (finding: binary files ingested as
# latin-1). PDFs are handled separately (extractable text); these have no text-decode path here.
_BINARY_EXTS = frozenset({
    ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp", ".rtf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp", ".ico", ".svgz",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".bz2", ".xz",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".flac", ".ogg", ".webm", ".mkv",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar", ".parquet", ".sqlite", ".db",
})
_SUPPORTED_HINT = "Supported: .txt, .md, .pdf, .csv, .json, .html and other UTF-8 text files."


def _csv_to_text(text: str) -> str:
    """CSV -> one header-qualified record per block ("col: value | col: value"), so each row is
    self-describing after chunking instead of an opaque comma soup. Falls back to raw text if it
    doesn't parse as tabular."""
    import csv
    import io

    try:
        rows = list(csv.reader(io.StringIO(text)))
    except Exception:  # noqa: BLE001 - not valid CSV -> ingest as-is
        return text
    if len(rows) < 2:
        return text
    header = [h.strip() for h in rows[0]]
    blocks: list[str] = []
    for row in rows[1:]:
        parts = [f"{(header[i] if i < len(header) else f'col{i + 1}')}: {v}"
                 for i, v in enumerate(row) if str(v).strip()]
        if parts:
            blocks.append(" | ".join(parts))
    return "\n\n".join(blocks) if blocks else text


def _json_record(item) -> str:
    import json

    if isinstance(item, dict):
        return " | ".join(
            f"{k}: {v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)}"
            for k, v in item.items()
        )
    return json.dumps(item, ensure_ascii=False)


def _json_to_text(text: str) -> str:
    """JSON -> one record per block. A list becomes one block per element; a top-level object
    with a single list value expands that list (the common {"items": [...]} shape); otherwise the
    object is one header-qualified block. Falls back to raw text if it doesn't parse."""
    import json

    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001 - not valid JSON -> ingest as-is
        return text
    if isinstance(data, dict):
        list_vals = [v for v in data.values() if isinstance(v, list)]
        if len(data) == 1 and list_vals:
            data = list_vals[0]
    if isinstance(data, list):
        blocks = [_json_record(x) for x in data]
        return "\n\n".join(b for b in blocks if b.strip()) or text
    return _json_record(data)


def _decode_upload(name: str, raw: bytes) -> str:
    """Turn uploaded bytes into ingestible text, or raise 422. Rejects known binary containers
    and null-byte binaries (finding: binaries decoded as latin-1 mojibake); strips HTML; parses
    CSV/JSON into per-record, header-qualified text (finding: tabular ingested opaquely)."""
    import os

    ext = os.path.splitext(name.lower())[1]
    if ext in _BINARY_EXTS:
        raise HTTPException(422, f"'{ext}' files aren't a readable text format. {_SUPPORTED_HINT}")
    # Null bytes are the strongest signal of a binary we don't recognize by extension.
    if b"\x00" in raw:
        raise HTTPException(422, f"File looks binary, not text. {_SUPPORTED_HINT}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Retry as latin-1 ONLY for genuinely-textual non-UTF-8 files; if the result is mostly
        # unprintable it was binary after all -> reject rather than embed garbage.
        text = raw.decode("latin-1", errors="replace")
        printable = sum(c.isprintable() or c.isspace() for c in text)
        if not text or printable / len(text) < 0.85:
            raise HTTPException(422, f"File isn't a readable text format. {_SUPPORTED_HINT}") from None
    if ext in (".html", ".htm"):
        text = _strip_html(text)
    elif ext == ".csv":
        text = _csv_to_text(text)
    elif ext == ".json":
        text = _json_to_text(text)
    if not text.strip():
        raise HTTPException(422, "File is empty or not a readable text format.")
    return text


async def _queue_ingest(session, tenant_id: str, src, *, reingest: bool = False, label: str = "ingest") -> None:
    """Spawn the background ingest for `src`; if the task ceiling rejects it, mark the source
    errored (spawn returns False and closes the coro) so it doesn't sit 'queued' indefinitely."""
    if not spawn(KnowledgeService.run_ingest_bg(tenant_id, src.id, reingest=reingest), name=f"{label}:{src.id}"):
        await KnowledgeService.mark_error(session, src, _QUEUE_FULL_MSG)


@router.get("/sources", response_model=list[KbSourceOut])
async def list_sources(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.list_sources(session, tenant_id, project_id)


@router.post("/sources", response_model=KbSourceOut, status_code=201)
async def add_source(project_id: str, body: KbSourceCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    src = await KnowledgeService.create_source(session, tenant_id, project_id, kind=body.kind, name=body.name, uri=body.uri, text=body.text, folder=body.folder, chunking_strategy=body.chunking_strategy, meta=body.meta)
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
    _: CurrentUser = Depends(require_role("editor")),
):
    """Upload a document file (.txt/.md/.pdf and other text formats) into the knowledge base."""
    # Bound the in-memory read so an oversized upload can't exhaust memory (audit M3): read one byte
    # past the cap to detect oversize without materializing the whole body.
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (max {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    name = file.filename or "upload"
    lower = name.lower()
    if lower.endswith(".pdf"):
        import io

        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(raw))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Could not read PDF: {e}") from e
        # Cap page count: a small but deeply-compressed / many-page PDF can make extract_text
        # explode CPU/memory (a decompression bomb) (audit M3).
        if len(reader.pages) > _MAX_PDF_PAGES:
            raise HTTPException(413, f"PDF has too many pages (max {_MAX_PDF_PAGES})")
        try:
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Could not read PDF: {e}") from e
        if not text:
            raise HTTPException(422, "PDF contains no extractable text (scanned image PDFs need OCR).")
    else:
        # Strip HTML, parse CSV/JSON into per-record text, and reject binaries with a clear 422.
        text = _decode_upload(name, raw)
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
                          tenant_id: str = Depends(current_tenant_id),
                          _: CurrentUser = Depends(require_role("editor"))):
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
                          tenant_id: str = Depends(current_tenant_id),
                          _: CurrentUser = Depends(require_role("editor"))):
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
async def update_source(project_id: str, source_id: str, body: dict, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
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
async def delete_source(project_id: str, source_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
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
async def dedupe_chunks(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
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
async def add_qa(project_id: str, body: QaPairCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    return await KnowledgeService.create_qa(session, tenant_id, project_id, question=body.question, answer=body.answer, kind=body.kind, tags=body.tags)


@qa_router.delete("/{qa_id}", status_code=204)
async def delete_qa(project_id: str, qa_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id), _: CurrentUser = Depends(require_role("editor"))):
    from sqlalchemy import select

    from forge.models import QaPair
    qa = (await session.execute(select(QaPair).where(QaPair.tenant_id == tenant_id, QaPair.id == qa_id))).scalar_one_or_none()
    if qa is None:
        raise HTTPException(404, "Q&A pair not found")
    await KnowledgeService.delete_qa(session, qa)
