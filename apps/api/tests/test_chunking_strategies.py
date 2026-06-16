"""Chunking strategies: pure splitter behavior + ingest wiring (strategy resolution
and chunk_size/overlap sourced from the project's rag_defaults)."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.knowledge.splitter import chunk_text
from forge.models import Project
from forge.services.knowledge import KnowledgeService

# --- pure splitter behavior ---


def test_recursive_strategy_caps_chunk_size():
    text = ("Sentence one. " * 200).strip()
    chunks = chunk_text(text, strategy="recursive", chunk_size=300, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 360 for c in chunks)  # ~chunk_size + slack


def test_section_strategy_splits_on_markdown_headers():
    doc = (
        "# Refunds\nRefunds go to the original method within 5-7 days.\n\n"
        "# Shipping\nOrders ship in 2 days.\n\n"
        "# Returns\nReturns accepted within 30 days."
    )
    chunks = chunk_text(doc, strategy="section", chunk_size=1000, overlap=100)
    assert len(chunks) == 3
    assert chunks[0].startswith("# Refunds") and "5-7 days" in chunks[0]
    assert any(c.startswith("# Shipping") for c in chunks)


def test_sentence_strategy_does_not_split_on_abbreviations():
    prose = "Dr. Smith met Mr. Brown at 3 p.m. in the U.S. capital. They signed the deal."
    chunks = chunk_text(prose, strategy="sentence", chunk_size=1000, overlap=0)
    # Two real sentences fit in one chunk; abbreviations must not create extra splits.
    assert len(chunks) == 1
    assert "Dr. Smith" in chunks[0] and "U.S. capital" in chunks[0]


def test_sentence_packs_to_chunk_size_with_overlap():
    prose = (
        "Alpha sentence here. Beta sentence here. Gamma sentence here. "
        "Delta sentence here. Epsilon sentence here. "
    ) * 4
    chunks = chunk_text(prose, strategy="sentence", chunk_size=120, overlap=30)
    assert len(chunks) > 1
    assert all(len(c) <= 130 for c in chunks)


def test_section_falls_back_to_recursive_without_headers():
    text = ("No headers here at all. " * 80).strip()
    chunks = chunk_text(text, strategy="section", chunk_size=200, overlap=40)
    assert len(chunks) > 1  # no headers -> recursive fallback still splits


def test_unknown_strategy_defaults_to_recursive():
    text = ("word " * 300).strip()
    assert chunk_text(text, strategy="nonsense", chunk_size=200) == chunk_text(
        text, strategy="recursive", chunk_size=200
    )


def test_empty_text_yields_no_chunks():
    assert chunk_text("", strategy="sentence") == []
    assert chunk_text("   ", strategy="section") == []


# --- ingest wiring ---


async def _make_project(slug: str, rag_defaults: dict) -> str:
    async with SessionLocal() as s:
        proj = Project(tenant_id="t_chunk", name="Chunk", slug=slug, config={"rag_defaults": rag_defaults})
        s.add(proj)
        await s.commit()
        await s.refresh(proj)
        return proj.id


async def test_ingest_uses_project_default_strategy(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma")
    pid = await _make_project("chunk-default-section", {"chunking_strategy": "section"})
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(
            s, "t_chunk", pid, kind="text", name="d", text="# A\nAlpha body.\n\n# B\nBeta body."
        )
        src = await KnowledgeService.ingest(s, src)
    assert src.status == "ready"
    assert src.chunking_strategy == "section"  # inherited project default, persisted on meta
    assert src.chunks == 2  # one chunk per markdown section


async def test_source_strategy_overrides_project_default(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma2")
    pid = await _make_project("chunk-override", {"chunking_strategy": "section"})
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(
            s, "t_chunk", pid, kind="text", name="d2",
            text="One sentence. Two sentence. Three sentence.", chunking_strategy="sentence",
        )
        src = await KnowledgeService.ingest(s, src)
    assert src.chunking_strategy == "sentence"  # per-source choice wins over project default


async def test_ingest_reads_chunk_size_from_rag_defaults(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma3")
    pid = await _make_project("chunk-size", {"chunk_size": 120, "chunk_overlap": 20, "chunking_strategy": "recursive"})
    long_text = ("This is a sentence about refunds and shipping policies. " * 40).strip()
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_chunk", pid, kind="text", name="d3", text=long_text)
        src = await KnowledgeService.ingest(s, src)
    # A small project chunk_size yields many chunks (far fewer at the 1000 default).
    assert src.chunks >= 10
