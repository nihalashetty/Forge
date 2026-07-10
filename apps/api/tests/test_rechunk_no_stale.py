"""Re-chunk / re-embed must REPLACE a source's vectors, never accumulate stale ones.

Guards the "chunk count keeps increasing every time I re-embed" class of bug: reingest deletes
the source's old vectors (in the current dim collection AND every known dim collection) before
re-ingesting, so the store row count always equals the source's reported chunk count.
"""
from __future__ import annotations

from forge.db.base import SessionLocal
from forge.knowledge.store import _where
from forge.models import Project
from forge.services.knowledge import KnowledgeService

_TEXT = ("Refunds go to the original card within five business days. Shipping takes two days. "
         "Error XJ9000 is a gateway timeout; retry after 30 seconds. Our office is in Berlin. ") * 5


async def _make_project(slug: str, rag_defaults: dict) -> str:
    async with SessionLocal() as s:
        proj = Project(tenant_id="t_rc", name="Rc", slug=slug, config={"rag_defaults": rag_defaults})
        s.add(proj)
        await s.commit()
        await s.refresh(proj)
        return proj.id


def _rows(embedder, pid, sid) -> int:
    return KnowledgeService._store(embedder).count_where(_where("t_rc", pid, [sid]))


async def test_rechunk_replaces_not_accumulates(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_rc")
    pid = await _make_project("rc", {"chunk_size": 200, "chunk_overlap": 0})
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rc", pid, kind="text", name="d", text=_TEXT)
        src = await KnowledgeService.ingest(s, src)
        embedder = await KnowledgeService.embedder_for_project(s, "t_rc", pid)
        assert _rows(embedder, pid, src.id) == src.chunks

        # Bigger chunks -> fewer rows; smaller -> more. Row count must track src.chunks each time,
        # proving the previous run's vectors were deleted, not left behind.
        src = await KnowledgeService.rechunk(s, src, chunk_size=4000, chunk_overlap=0)
        assert _rows(embedder, pid, src.id) == src.chunks
        src = await KnowledgeService.rechunk(s, src, chunk_size=150, chunk_overlap=0)
        assert _rows(embedder, pid, src.id) == src.chunks


async def test_reembed_same_settings_stays_flat(tmp_path):
    """The exact 'Apply & re-embed' path (run_ingest_bg with reingest=True) repeated with
    UNCHANGED settings must not grow the row count."""
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_bg")
    pid = await _make_project("bg", {"chunk_size": 180, "chunk_overlap": 0})
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rc", pid, kind="text", name="d", text=_TEXT)
        sid = src.id
        embedder = await KnowledgeService.embedder_for_project(s, "t_rc", pid)

    counts = []
    for _ in range(3):
        await KnowledgeService.run_ingest_bg("t_rc", sid, reingest=True)
        counts.append(_rows(embedder, pid, sid))
    assert counts[0] == counts[1] == counts[2], f"re-embed accumulated stale rows: {counts}"


async def test_dedupe_removes_exact_duplicate_chunks(tmp_path):
    """The same document ingested twice -> identical chunks; dedupe keeps one copy of each and
    fixes the affected sources' counts."""
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_dedupe")
    pid = await _make_project("dedupe", {"chunk_size": 200, "chunk_overlap": 0})
    async with SessionLocal() as s:
        a = await KnowledgeService.create_source(s, "t_rc", pid, kind="text", name="dup.md", text=_TEXT)
        a = await KnowledgeService.ingest(s, a)
        b = await KnowledgeService.create_source(s, "t_rc", pid, kind="text", name="dup-copy.md", text=_TEXT)
        b = await KnowledgeService.ingest(s, b)
        embedder = await KnowledgeService.embedder_for_project(s, "t_rc", pid)

        before = _rows(embedder, pid, a.id) + _rows(embedder, pid, b.id)
        res = await KnowledgeService.dedupe_chunks(s, "t_rc", pid)
        after = _rows(embedder, pid, a.id) + _rows(embedder, pid, b.id)

    # Two identical docs -> every chunk had exactly one duplicate; half are removed.
    assert res["removed"] == before // 2
    assert res["remaining"] == after
    # A second run is a no-op (idempotent).
    async with SessionLocal() as s:
        res2 = await KnowledgeService.dedupe_chunks(s, "t_rc", pid)
    assert res2["removed"] == 0
