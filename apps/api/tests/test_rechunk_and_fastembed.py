"""Per-source re-chunking overrides (strategy / size / overlap) and the local
open-source fastembed embedder resolution."""

from __future__ import annotations

import pytest

from forge.db.base import SessionLocal
from forge.knowledge.embeddings import resolve_embedder
from forge.services.knowledge import KnowledgeService

# --- re-chunk overrides ---

_BIG = " ".join(f"Refund policy note {i}: widgets ship within five business days." for i in range(120))


async def test_ingest_records_chunk_settings(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_rec")
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rec", "p_rec", kind="text", name="big", text=_BIG)
        src = await KnowledgeService.ingest(s, src)
    # Defaults (no project rag_defaults) are recorded in meta so the UI can display them.
    assert src.chunking_strategy == "recursive"
    assert src.chunk_size == 1000
    assert src.chunk_overlap == 200
    assert src.chunks >= 1


async def test_rechunk_smaller_size_yields_more_chunks(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_rc")
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rc", "p_rc", kind="text", name="big", text=_BIG)
        src = await KnowledgeService.ingest(s, src)
        base = src.chunks
        # Shrinking the chunk size must split the same text into more pieces...
        src = await KnowledgeService.rechunk(s, src, chunk_size=200, chunk_overlap=20)
    assert src.chunk_size == 200
    assert src.chunk_overlap == 20
    assert src.chunks > base


async def test_rechunk_honors_zero_overlap(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_ov0")
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_ov0", "p_ov0", kind="text", name="big", text=_BIG)
        src = await KnowledgeService.ingest(s, src)
        # An explicit 0 overlap must stick (not silently reset to the 200 default).
        src = await KnowledgeService.rechunk(s, src, chunk_size=300, chunk_overlap=0)
    assert src.chunk_overlap == 0
    assert src.chunk_size == 300


async def test_rechunk_strategy_only_preserves_size(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_rcs")
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rcs", "p_rcs", kind="text", name="big", text=_BIG)
        src = await KnowledgeService.ingest(s, src)
        # ...while a strategy-only re-chunk leaves size/overlap untouched (None = keep).
        src = await KnowledgeService.rechunk(s, src, chunking_strategy="sentence")
    assert src.chunking_strategy == "sentence"
    assert src.chunk_size == 1000
    assert src.chunk_overlap == 200


# --- fastembed resolution (local, open-source) ---


def test_fastembed_resolves_local_model():
    pytest.importorskip("fastembed")
    try:
        e = resolve_embedder("fastembed:BAAI/bge-small-en-v1.5")
    except RuntimeError:
        # No toy fallback anymore - resolve_embedder raises if the model can't load
        # (e.g. offline with no cached model). Skip rather than fail the suite.
        pytest.skip("fastembed model could not be loaded (offline)")
    assert e.name == "BAAI/bge-small-en-v1.5"
    assert e.dim == 384
    assert len(e.embed_query("hello world")) == 384


def test_default_embedder_is_local_fastembed():
    """With no model ref, resolve_embedder returns the local open-source default
    (no FakeEmbedder). Guards the 'remove fake embedder' decision."""
    pytest.importorskip("fastembed")
    try:
        e = resolve_embedder(None)
    except RuntimeError:
        pytest.skip("fastembed model could not be loaded (offline)")
    assert e.name == "BAAI/bge-small-en-v1.5" and e.dim == 384
