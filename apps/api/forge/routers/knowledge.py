"""Knowledge endpoints — sources (ingest text/url), Q&A pairs, and a search debugger."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.schemas.dto import (
    KbSourceCreate,
    KbSourceOut,
    KnowledgeSearchIn,
    QaPairCreate,
    QaPairOut,
)
from forge.services.knowledge import KnowledgeService

router = APIRouter(prefix="/v1/projects/{project_id}/knowledge", tags=["knowledge"])
qa_router = APIRouter(prefix="/v1/projects/{project_id}/qa-pairs", tags=["knowledge"])


@router.get("/sources", response_model=list[KbSourceOut])
async def list_sources(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.list_sources(session, tenant_id, project_id)


@router.post("/sources", response_model=KbSourceOut, status_code=201)
async def add_source(project_id: str, body: KbSourceCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    src = await KnowledgeService.create_source(session, tenant_id, project_id, kind=body.kind, name=body.name, uri=body.uri, text=body.text, folder=body.folder, chunking_strategy=body.chunking_strategy)
    return await KnowledgeService.ingest(session, src)  # synchronous ingest for the slice


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
    return await KnowledgeService.ingest(session, src)


@router.get("/folders", response_model=list[str])
async def list_folders(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await KnowledgeService.list_folders(session, tenant_id, project_id)


@router.get("/health")
async def embedding_health(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Embedding-dimension health: flags sources embedded with a different model than the
    project's current embedder (which would silently return no search results)."""
    return await KnowledgeService.embedding_health(session, tenant_id, project_id)


@router.post("/sources/{source_id}/reingest")
async def reingest_source(project_id: str, source_id: str, session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id)):
    """Re-fetch + re-embed a source (re-crawl a site, or re-embed under the current model)."""
    from sqlalchemy import select

    from forge.models import KbSource
    src = (await session.execute(select(KbSource).where(KbSource.tenant_id == tenant_id, KbSource.id == source_id))).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="source not found")
    src = await KnowledgeService.reingest(session, src)
    return {"id": src.id, "status": src.status, "chunks": src.chunks}


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
    hits = await KnowledgeService.search(session, tenant_id, project_id, body.query, top_k=body.top_k, folders=body.folders)
    return [{"text": h.text, "score": round(h.score, 4), "source_id": h.metadata.get("source_id")} for h in hits]


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
