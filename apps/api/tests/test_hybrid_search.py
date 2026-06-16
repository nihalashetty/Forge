"""Hybrid retrieval: RRF fusion + BM25 primitives and the end-to-end search(hybrid=True)
path (scoping, score normalization, source filtering, graceful vector fallback)."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.knowledge.hybrid import bm25_rank, rrf_fuse
from forge.services.knowledge import KnowledgeService

# --- pure primitives ---


def test_rrf_fuse_rewards_agreement():
    # "b" is near the top of BOTH lists; "a" and "z" each top only one.
    fused = rrf_fuse(["a", "b", "c"], ["z", "b", "y"])
    assert fused["b"] > fused["a"]
    assert fused["b"] > fused["z"]


def test_bm25_rank_surfaces_exact_term():
    docs = [
        ("d1", "general refund and shipping policy details"),
        ("d2", "error code XJ9000 means a payment gateway timeout"),
        ("d3", "how to contact our support team"),
    ]
    assert bm25_rank("XJ9000 gateway timeout", docs)[0] == "d2"


def test_bm25_rank_empty_when_no_overlap():
    docs = [("d1", "alpha beta gamma"), ("d2", "delta epsilon zeta")]
    assert bm25_rank("zzz qqq wwww", docs) == []


# --- end-to-end search(hybrid=True) ---


async def test_hybrid_search_scoped_and_normalized(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma")
    async with SessionLocal() as s:
        for i, t in enumerate([
            "Refunds go to the original payment method within 5-7 business days.",
            "Error code XJ9000 indicates a payment gateway timeout; retry after 30 seconds.",
            "Cancel an order from the Orders page before the item ships.",
        ]):
            src = await KnowledgeService.create_source(s, "t_hy", "p_hy", kind="text", name=f"d{i}", text=t)
            await KnowledgeService.ingest(s, src)
        # A different project must never leak into p_hy's results.
        other = await KnowledgeService.create_source(s, "t_hy", "p_other", kind="text", name="x", text="XJ9000 belongs to another project")
        await KnowledgeService.ingest(s, other)
        hits = await KnowledgeService.search(s, "t_hy", "p_hy", "XJ9000 timeout", top_k=3, hybrid=True)
    assert hits
    assert all(0 < h.score <= 1.0 for h in hits)  # normalized fusion score
    assert hits[0].score == 1.0  # best fused result anchors at 1.0
    assert all(h.metadata.get("project_id") == "p_hy" for h in hits)  # tenant/project scoped
    assert any("XJ9000" in h.text for h in hits)  # lexical match surfaced


async def test_hybrid_respects_source_filter(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma2")
    async with SessionLocal() as s:
        a = await KnowledgeService.create_source(s, "t_sf", "p_sf", kind="text", name="a", text="XJ9000 appears in source A only")
        await KnowledgeService.ingest(s, a)
        b = await KnowledgeService.create_source(s, "t_sf", "p_sf", kind="text", name="b", text="source B is about refunds and shipping")
        await KnowledgeService.ingest(s, b)
        hits = await KnowledgeService.search(s, "t_sf", "p_sf", "XJ9000", top_k=5, hybrid=True, source_ids=[a.id])
    assert hits
    assert all(h.metadata.get("source_id") == a.id for h in hits)  # filter preserved under hybrid


async def test_hybrid_degrades_to_vector_without_lexical_overlap(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma3")
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_dg", "p_dg", kind="text", name="d", text="Refunds are issued within five business days.")
        await KnowledgeService.ingest(s, src)
        # Query shares no tokens with the corpus -> BM25 contributes nothing -> vector fallback.
        hits = await KnowledgeService.search(s, "t_dg", "p_dg", "zzz qqq wwww", top_k=3, hybrid=True)
    assert len(hits) == 1  # no crash; vector path still returns the doc
    assert "Refunds" in hits[0].text
