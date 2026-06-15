"""Phase 5 validation: splitter, offline embedder, Q&A lookup, Chroma ingest→search."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.knowledge.embeddings import FakeEmbedder, cosine
from forge.knowledge.splitter import split_text
from forge.services.knowledge import KnowledgeService


def test_splitter_chunks_long_text():
    text = ("Sentence one. " * 200).strip()
    chunks = split_text(text, chunk_size=300, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 360 for c in chunks)  # ~chunk_size + overlap slack


def test_embedder_similarity_reflects_overlap():
    e = FakeEmbedder()
    a = e.embed_query("refunds are issued to the original payment method")
    b = e.embed_query("how long do refunds take to be issued")
    c = e.embed_query("the weather in tokyo is sunny today")
    assert cosine(a, b) > cosine(a, c)  # topical overlap > unrelated


async def test_qa_create_and_lookup():
    async with SessionLocal() as s:
        await KnowledgeService.create_qa(s, "t_qa", "p_qa", question="How do I reset my password?", answer="Settings > Security > Reset password.", kind="faq")
        match = await KnowledgeService.lookup(s, "t_qa", "p_qa", "how to reset password", threshold=0.2)
    assert match and "Security" in match["answer"]


async def test_ingest_text_and_search(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma")  # isolate Chroma for the test
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(
            s, "t_kb", "p_kb", kind="text", name="help",
            text="Refunds are issued to the original payment method within 5-7 business days. "
                 "To cancel an order, open the Orders page before it ships.",
        )
        src = await KnowledgeService.ingest(s, src)
        assert src.status == "ready" and src.chunks >= 1
        hits = await KnowledgeService.search(s, "t_kb", "p_kb", "how long do refunds take", top_k=3)
    assert hits, "expected at least one hit"
    assert "refund" in hits[0].text.lower()
