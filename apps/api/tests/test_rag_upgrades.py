"""Production RAG upgrades: cross-encoder rerank (graceful), native semantic chunking,
and parent-child retrieval. Q&A logic is deliberately NOT exercised here - it is unchanged."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.knowledge.rerank import _sigmoid, rerank_hits
from forge.knowledge.splitter import _percentile, chunk_text
from forge.knowledge.store import Hit
from forge.models import Project
from forge.services.knowledge import KnowledgeService

# --- reranker: pure + graceful degradation (no model download needed) ---


def test_sigmoid_bounds():
    assert _sigmoid(0.0) == 0.5
    assert _sigmoid(20) > 0.99 and _sigmoid(-20) < 0.01


def test_rerank_missing_model_is_identity_truncated_to_top_k():
    hits = [Hit(id=str(i), text=f"doc {i}", score=0.9 - i * 0.1, metadata={}) for i in range(5)]
    out = rerank_hits("anything", hits, top_k=3, model="does/not-exist")
    assert [h.id for h in out] == ["0", "1", "2"]  # unchanged order, capped


def test_rerank_empty_hits_and_empty_query():
    assert rerank_hits("q", [], top_k=3) == []
    hits = [Hit(id="a", text="x", score=0.5, metadata={})]
    assert rerank_hits("  ", hits, top_k=3) == hits  # blank query short-circuits


def test_rerank_reorders_by_relevance():
    """Real cross-encoder (small, cached after first run) must lift the relevant doc."""
    hits = [
        Hit(id="weather", text="The weather in Paris is mild in spring.", score=0.9, metadata={}),
        Hit(id="fruit", text="Bananas are a good source of potassium.", score=0.8, metadata={}),
        Hit(id="reset", text="To reset your password, use the Forgot Password link on the login screen.", score=0.1, metadata={}),
    ]
    out = rerank_hits("how do I reset my password?", hits, top_k=2)
    assert out[0].id == "reset"
    assert 0.0 <= out[0].score <= 1.0


# --- semantic chunking: pure, deterministic via a fake embedder ---


def test_percentile_interpolates():
    assert _percentile([0.0, 1.0], 50) == 0.5
    assert _percentile([1.0], 95) == 1.0
    assert _percentile([], 95) == 0.0


def test_semantic_splits_on_topic_shift():
    # A fake embedder: refund sentences -> [1,0]; rocket sentences -> [0,1]. The single
    # boundary between the two topics is the sharpest similarity drop -> exactly one cut.
    refunds = "Refunds are issued to your original card. We process them within five days. Contact support for status."
    rockets = "Rockets burn liquid oxygen. The first stage separates after ascent. Reentry heats the shield."
    text = refunds + " " + rockets

    def fake_embed(sentences):
        return [[1.0, 0.0] if "efund" in s or "process" in s or "support" in s else [0.0, 1.0] for s in sentences]

    chunks = chunk_text(text, strategy="semantic", chunk_size=1000, overlap=0, embed_fn=fake_embed)
    assert len(chunks) == 2
    assert "Refunds" in chunks[0] and "Rockets" in chunks[1]


def test_semantic_without_embed_fn_falls_back_to_recursive():
    text = ("Sentence one here. Sentence two here. Sentence three here. " * 20).strip()
    got = chunk_text(text, strategy="semantic", chunk_size=200, overlap=40)
    expected = chunk_text(text, strategy="recursive", chunk_size=200, overlap=40)
    assert got == expected


def test_semantic_too_few_sentences_falls_back():
    def boom(_s):
        raise AssertionError("embed_fn must not be called for <3 sentences")

    assert chunk_text("Only one sentence here.", strategy="semantic", embed_fn=boom) == ["Only one sentence here."]


# --- semantic + parent-child: ingest wiring (uses the real local embedder) ---


async def _make_project(slug: str, rag_defaults: dict) -> str:
    async with SessionLocal() as s:
        proj = Project(tenant_id="t_rag", name="Rag", slug=slug, config={"rag_defaults": rag_defaults})
        s.add(proj)
        await s.commit()
        await s.refresh(proj)
        return proj.id


async def test_semantic_ingest_does_not_crash(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_sem")
    pid = await _make_project("rag-semantic", {"chunking_strategy": "semantic"})
    text = ("Refunds go to the original card within five business days. Shipping takes two days. "
            "Error XJ9000 is a gateway timeout; retry after 30 seconds. Our office is in Berlin. ") * 3
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rag", pid, kind="text", name="d", text=text)
        src = await KnowledgeService.ingest(s, src)
    assert src.status == "ready"
    assert src.chunks >= 1
    assert src.chunking_strategy == "semantic"


async def test_parent_child_ingest_and_retrieval(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_pc")
    pid = await _make_project("rag-parentchild", {"retrieval_mode": "parent_child", "chunk_size": 400, "child_chunk_size": 120})
    parent_a = ("Refund policy. Refunds are issued to the original payment method within five to "
                "seven business days once the returned item is received and inspected at our warehouse.")
    parent_b = ("Shipping policy. Standard orders ship within two business days and arrive in about "
                "a week; expedited shipping is available at checkout for an additional fee.")
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rag", pid, kind="text", name="policies",
                                                   text=parent_a + "\n\n" + parent_b)
        src = await KnowledgeService.ingest(s, src)
        assert src.status == "ready"
        assert (src.meta or {}).get("retrieval_mode") == "parent_child"
        assert (src.meta or {}).get("parents", 0) >= 1
        assert src.chunks >= (src.meta or {}).get("parents")  # at least one child per parent

        hits = await KnowledgeService.search(s, "t_rag", pid, "how long does a refund take?", top_k=2)
    assert hits
    top = hits[0]
    # Retrieval returns the PARENT window (the full paragraph), not just the matched child slice.
    assert "five to seven business days" in top.text
    assert top.metadata.get("parent_id")
    # De-dup by parent: no two returned hits share a parent_id.
    parent_ids = [h.metadata.get("parent_id") for h in hits]
    assert len(parent_ids) == len(set(parent_ids))


async def test_chunk_map_projects_and_marks_retrieved(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_map")
    pid = await _make_project("rag-map", {"chunk_size": 200})
    async with SessionLocal() as s:
        for i, t in enumerate([
            "Refunds go to the original payment method within five business days.",
            "Error code XJ9000 indicates a payment gateway timeout; retry after 30 seconds.",
            "Standard orders ship within two business days from our warehouse.",
        ]):
            src = await KnowledgeService.create_source(s, "t_rag", pid, kind="text", name=f"d{i}", text=t)
            await KnowledgeService.ingest(s, src)
        res = await KnowledgeService.chunk_map(s, "t_rag", pid, query="how long for a refund?", top_k=2)
    assert res["total"] >= 3
    assert len(res["points"]) >= 3
    # every point has 2-D coords + a source
    for p in res["points"]:
        assert isinstance(p["x"], float) and isinstance(p["y"], float)
        assert p["source_id"]
    assert res["query_point"] and len(res["query_point"]) == 2
    assert res["sources"]  # legend populated
    # the query overlay tagged at least one chunk as retrieved (rank 1..top_k)
    ranks = [p.get("retrieved") for p in res["points"] if p.get("retrieved")]
    assert ranks and min(ranks) == 1


async def test_chunk_map_empty_project_is_safe(tmp_path):
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_map_empty")
    pid = await _make_project("rag-map-empty", {})
    async with SessionLocal() as s:
        res = await KnowledgeService.chunk_map(s, "t_rag", pid, query="anything")
    assert res == {"points": [], "sources": [], "query_point": None, "query": "anything", "total": 0, "truncated": False}


async def test_flat_mode_unchanged(tmp_path):
    """Default (no retrieval_mode) still returns plain chunks with no parent metadata."""
    from forge.config import settings

    settings.chroma_path = str(tmp_path / "chroma_flat")
    pid = await _make_project("rag-flat", {})
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_rag", pid, kind="text", name="d",
                                                   text="Refunds are issued within five business days.")
        await KnowledgeService.ingest(s, src)
        hits = await KnowledgeService.search(s, "t_rag", pid, "refund time", top_k=3)
    assert hits
    assert not hits[0].metadata.get("parent_id")  # flat: no parent-child metadata
