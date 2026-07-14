"""Audit fixes for the RAG relevance floor + ingestion/retrieval gaps.

Covers, per the feature audit:
- effective cosine grounding floor (calibrated for the default BGE embedder) end to end,
- min_score thresholding the TRUE cosine in hybrid mode (not the fused rank),
- source-provenance citations persisted on chunks,
- crawl honoring robots.txt + max_depth,
- CSV/JSON per-record parsing + binary-upload rejection + HTML stripping,
- chunk_size clamped to the embedder's input limit,
- long-term-memory recall similarity floor.

Model-backed tests use the local fastembed embedder and skip cleanly when it can't load
(offline), mirroring the other knowledge tests.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from forge.knowledge.crawl import MAX_DEPTH_CAP, MAX_PAGES_CAP
from forge.knowledge.embeddings import DEFAULT_MIN_SCORE, DEFAULT_RERANK_MIN_SCORE, _max_input_chars
from forge.knowledge.store import Hit, citation_for
from forge.nodes.rag import _passes_floor
from forge.routers.knowledge import _csv_to_text, _decode_upload, _json_to_text


def _require_embedder():
    """Return the local BGE embedder or skip (offline / model not cached)."""
    pytest.importorskip("fastembed")
    from forge.knowledge.embeddings import resolve_embedder

    try:
        e = resolve_embedder(None)
    except Exception:  # noqa: BLE001
        pytest.skip("fastembed model could not be loaded (offline)")
    if getattr(e, "name", "") != "BAAI/bge-small-en-v1.5":
        pytest.skip("fastembed model unavailable")
    return e


# --- calibrated floor defaults (pure) ---

def test_default_floors_are_calibrated():
    # BGE unrelated pairs measure ~0.4-0.52, related ~0.75+, so the floor must sit between.
    assert 0.55 <= DEFAULT_MIN_SCORE <= 0.7
    assert 0.0 < DEFAULT_RERANK_MIN_SCORE < DEFAULT_MIN_SCORE


# --- min_score on the right scale (pure) ---

def test_passes_floor_uses_cosine_not_fused_rank():
    # vector-only: Hit.score IS the cosine.
    assert _passes_floor(Hit("a", "t", 0.70, {}), 0.6, hybrid=False) is True
    assert _passes_floor(Hit("a", "t", 0.50, {}), 0.6, hybrid=False) is False
    # hybrid: Hit.score is the fused rank (top≈1.0); the floor must use vector_score.
    assert _passes_floor(Hit("a", "t", 1.0, {}, vector_score=0.50), 0.6, hybrid=True) is False
    assert _passes_floor(Hit("a", "t", 1.0, {}, vector_score=0.70), 0.6, hybrid=True) is True
    # a BM25-only hit has no cosine -> kept (a strong exact-term match isn't floored out).
    assert _passes_floor(Hit("a", "t", 0.9, {}, vector_score=None), 0.6, hybrid=True) is True


# --- citations from chunk provenance (pure) ---

def test_citation_for_prefers_page_then_source():
    assert citation_for({"page_url": "https://x.test/pricing", "page_title": "pricing"}) == "pricing — https://x.test/pricing"
    assert citation_for({"page_url": "https://x.test/p"}) == "https://x.test/p"
    assert citation_for({"source_name": "help", "source_uri": "https://x.test/help"}) == "help — https://x.test/help"
    assert citation_for({"source_name": "manual"}) == "manual"
    assert citation_for({}) == ""
    assert citation_for(None) == ""


# --- BM25 cache split: index build + score parity (pure) ---

def test_bm25_build_and_score_match_one_shot():
    pytest.importorskip("rank_bm25")
    from forge.knowledge.hybrid import bm25_rank, bm25_scores, build_bm25

    docs = [
        ("d1", "general refund and shipping policy details"),
        ("d2", "error code XJ9000 means a payment gateway timeout"),
        ("d3", "how to contact our support team"),
    ]
    idx = build_bm25(docs)
    assert bm25_scores(idx, "XJ9000 gateway timeout")[0] == "d2"
    # The cached-index path must match the one-shot bm25_rank exactly.
    assert bm25_scores(idx, "XJ9000 gateway timeout") == bm25_rank("XJ9000 gateway timeout", docs)
    assert bm25_scores(idx, "zzz qqq") == []
    assert bm25_scores(None, "anything") == []
    assert build_bm25([]) is None


# --- chunk_size vs embedder token limit (pure) ---

def test_max_input_chars_by_model():
    assert _max_input_chars("BAAI/bge-small-en-v1.5") == 512 * 4
    assert _max_input_chars("text-embedding-3-small") == 8191 * 4
    assert _max_input_chars("some-unknown-model") == 512 * 4  # conservative default


# --- CSV/JSON per-record parsing + upload guards (pure) ---

def test_csv_parsed_into_header_qualified_records():
    out = _csv_to_text("name,plan,seats\nAcme,enterprise,50\nBeta,free,3")
    assert "name: Acme" in out and "plan: enterprise" in out and "seats: 50" in out
    assert "name: Beta" in out
    assert "\n\n" in out  # records separated so the chunker can split on record boundaries


def test_csv_without_data_rows_falls_back():
    assert _csv_to_text("only one line") == "only one line"


def test_json_list_of_objects_becomes_records():
    out = _json_to_text('[{"q": "hi", "a": "there"}, {"q": "bye", "a": "now"}]')
    assert "q: hi | a: there" in out
    assert "q: bye | a: now" in out
    assert "\n\n" in out


def test_json_single_list_value_is_expanded():
    out = _json_to_text('{"items": [{"k": 1}, {"k": 2}]}')
    assert "k: 1" in out and "k: 2" in out


def test_json_invalid_falls_back_to_raw():
    assert _json_to_text("not json {{{") == "not json {{{"


def test_decode_upload_rejects_binary_extension():
    with pytest.raises(HTTPException) as ei:
        _decode_upload("report.docx", b"PK\x03\x04 not really text")
    assert ei.value.status_code == 422


def test_decode_upload_rejects_null_byte_binary():
    with pytest.raises(HTTPException):
        _decode_upload("mystery.dat", b"text\x00\x00\x01\x02 binary")


def test_decode_upload_strips_html():
    out = _decode_upload("page.html", b"<html><body><h1>Hi</h1><p>There</p></body></html>")
    assert "Hi" in out and "There" in out
    assert "<h1>" not in out and "<body>" not in out


def test_decode_upload_plain_text_and_csv_dispatch():
    assert _decode_upload("notes.txt", b"just some notes") == "just some notes"
    out = _decode_upload("data.csv", b"a,b\n1,2")
    assert "a: 1" in out and "b: 2" in out


# --- crawl: robots.txt + depth (monkeypatched network, offline) ---

def test_crawl_caps_are_bounded():
    assert MAX_PAGES_CAP <= 500 and MAX_DEPTH_CAP <= 10


async def test_crawl_honors_robots_and_max_depth(monkeypatch):
    import forge.util.ssrf as ssrf
    from forge.knowledge import crawl as crawl_mod

    class _Resp:
        def __init__(self, text: str, status: int = 200) -> None:
            self.text = text
            self.status_code = status

    site = {
        "https://acme.test/robots.txt": _Resp("User-agent: *\nDisallow: /private\n"),
        "https://acme.test/": _Resp('<a href="/a">A</a> <a href="/private">P</a> <a href="/b">B</a>'),
        "https://acme.test/a": _Resp('<a href="/c">C</a> alpha body'),
        "https://acme.test/b": _Resp("bee body"),
        "https://acme.test/c": _Resp("cee body"),
        "https://acme.test/private": _Resp("secret body"),
    }

    async def fake_get(client, url, **kw):
        if url in site:
            return site[url]
        raise RuntimeError("404")

    monkeypatch.setattr(ssrf, "guarded_get", fake_get)
    pages = await crawl_mod.crawl_site("https://acme.test/", max_pages=50, max_depth=1, delay=0.0)

    assert "https://acme.test/" in pages
    assert "https://acme.test/a" in pages and "https://acme.test/b" in pages
    assert "https://acme.test/private" not in pages  # robots.txt Disallow honored
    assert "https://acme.test/c" not in pages  # one hop beyond max_depth=1


# --- model-backed end-to-end ---

async def test_offtopic_query_is_floored_and_on_topic_cites(tmp_path):
    _require_embedder()
    from langchain_core.messages import SystemMessage

    from forge.config import settings
    from forge.db.base import SessionLocal
    from forge.engine.context import CompileContext
    from forge.nodes.rag import retrieval_factory
    from forge.services.knowledge import KnowledgeService

    settings.chroma_path = str(tmp_path / "chroma_floor")
    t, p = "t_floor", "p_floor"
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(
            s, t, p, kind="text", name="refunds",
            text="Refunds are issued to the original payment method within 5-7 business days.",
        )
        await KnowledgeService.ingest(s, src)

    node = retrieval_factory({"announce_empty": True, "top_k": 3}, CompileContext(tenant_id=t, project_id=p))
    off = await node({"messages": [{"role": "user", "content": "what is the capital of France?"}]})
    assert isinstance(off["messages"][-1], SystemMessage)
    assert "no relevant" in off["messages"][-1].content.lower()  # off-topic floored -> empty note

    on = await node({"messages": [{"role": "user", "content": "how long do refunds take?"}]})
    body = on["messages"][-1].content
    assert "KNOWLEDGE BASE context" in body
    assert "Refunds" in body
    assert "refunds" in body.lower().split("] ")[0]  # citation label carries the source name


async def test_hybrid_hit_carries_true_cosine_vector_score(tmp_path):
    _require_embedder()
    from forge.config import settings
    from forge.db.base import SessionLocal
    from forge.services.knowledge import KnowledgeService

    settings.chroma_path = str(tmp_path / "chroma_hy_vs")
    t, p = "t_hyvs", "p_hyvs"
    async with SessionLocal() as s:
        for i, txt in enumerate([
            "Refunds go to the original card within five business days.",
            "Error code XJ9000 indicates a payment gateway timeout; retry after 30 seconds.",
            "Cancel an order from the Orders page before it ships.",
        ]):
            src = await KnowledgeService.create_source(s, t, p, kind="text", name=f"d{i}", text=txt)
            await KnowledgeService.ingest(s, src)
        hits = await KnowledgeService.search(s, t, p, "XJ9000 timeout", top_k=3, hybrid=True)
    assert hits
    assert hits[0].score == 1.0  # fused rank still normalized to 1.0 at the top
    # vector_score is the underlying cosine (0..1), a different scale from the fused score.
    assert any(h.vector_score is not None for h in hits)
    assert all(h.vector_score is None or 0.0 <= h.vector_score <= 1.0 for h in hits)


async def test_ingest_persists_source_citation_metadata(tmp_path):
    _require_embedder()
    from forge.config import settings
    from forge.db.base import SessionLocal
    from forge.services.knowledge import KnowledgeService

    settings.chroma_path = str(tmp_path / "chroma_cite")
    t, p = "t_cite", "p_cite"
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(
            s, t, p, kind="text", name="Refund Policy",
            text="Refunds are issued to the original payment method within five business days.",
        )
        await KnowledgeService.ingest(s, src)
        hits = await KnowledgeService.search(s, t, p, "refund timing", top_k=2)
    assert hits
    assert hits[0].metadata.get("source_name") == "Refund Policy"
    assert hits[0].metadata.get("embedding_model") == "BAAI/bge-small-en-v1.5"
    assert citation_for(hits[0].metadata) == "Refund Policy"


async def test_ingest_clamps_chunk_size_to_embedder_limit(tmp_path):
    _require_embedder()
    from forge.config import settings
    from forge.db.base import SessionLocal
    from forge.models import Project
    from forge.services.knowledge import KnowledgeService

    settings.chroma_path = str(tmp_path / "chroma_clamp")
    async with SessionLocal() as s:
        proj = Project(tenant_id="t_cl", name="Cl", slug="clamp", config={"rag_defaults": {"chunk_size": 100000}})
        s.add(proj)
        await s.commit()
        await s.refresh(proj)
        src = await KnowledgeService.create_source(s, "t_cl", proj.id, kind="text", name="big", text="word " * 800)
        src = await KnowledgeService.ingest(s, src)
    assert src.status == "ready"
    assert src.chunk_size <= _max_input_chars("BAAI/bge-small-en-v1.5")  # clamped down
    assert (src.meta or {}).get("chunk_size_requested") == 100000  # original recorded


async def test_memory_recall_similarity_floor(tmp_path):
    _require_embedder()
    from forge.config import settings
    from forge.db.base import SessionLocal
    from forge.services.memory import MemoryService

    settings.chroma_path = str(tmp_path / "chroma_memfloor")
    t, p = "t_memf", "p_memf"
    async with SessionLocal() as s:
        await MemoryService.remember(s, t, p, "Our refund window is 30 days.")

    original = settings.memory_recall_min_score
    try:
        settings.memory_recall_min_score = 0.6
        async with SessionLocal() as s:
            off = await MemoryService.recall(s, t, p, "the capital of France", top_k=5)
        assert off == []  # unrelated memory filtered by the floor
        settings.memory_recall_min_score = 0.0
        async with SessionLocal() as s:
            on = await MemoryService.recall(s, t, p, "the capital of France", top_k=5)
        assert on  # floor off (default) -> nearest returned regardless of distance
    finally:
        settings.memory_recall_min_score = original
