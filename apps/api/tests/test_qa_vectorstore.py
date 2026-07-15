"""Q&A semantic match now runs through the vector store (forge_qa_<dim>), not a
Python O(n) cosine over every row."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.models import QaPair
from forge.services.knowledge import KnowledgeService

T, P = "t_qa_vs", "p_qa_vs"


async def _seed():
    async with SessionLocal() as s:
        await KnowledgeService.create_qa(s, T, P, question="How do I reset my password?", answer="Use the reset link on the login page.", kind="faq")
        await KnowledgeService.create_qa(s, T, P, question="What are your support hours?", answer="9am-5pm ET, Mon-Fri.", kind="hours")


async def test_top_qa_ranks_semantically():
    await _seed()
    async with SessionLocal() as s:
        hits = await KnowledgeService.top_qa(s, T, P, "I forgot my password and need to reset it", top_k=1, threshold=0.0)
    assert hits and "reset link" in hits[0]["answer"]


async def test_lookup_kind_filter():
    await _seed()
    async with SessionLocal() as s:
        # restricting to the 'hours' kind must not return the password FAQ
        hit = await KnowledgeService.lookup(s, T, P, "reset password", threshold=0.0, kinds=["hours"])
    assert hit is not None and "9am-5pm" in hit["answer"]


async def test_lazy_reindex_backfills_existing_rows():
    # Insert a QaPair directly (bypassing create_qa's upsert) to simulate pre-existing data,
    # then top_qa must still find it via the lazy reindex.
    async with SessionLocal() as s:
        emb = await (await KnowledgeService.embedder_for_project(s, "t_bf", "p_bf")).aembed_query("billing question")
        s.add(QaPair(tenant_id="t_bf", project_id="p_bf", question="How do I update my billing card?",
                     answer="Settings → Billing → Update card.", kind="faq", q_embedding=emb))
        await s.commit()
    async with SessionLocal() as s:
        hits = await KnowledgeService.top_qa(s, "t_bf", "p_bf", "change my billing card", top_k=1, threshold=0.0)
    assert hits and "Billing" in hits[0]["answer"]


async def test_update_qa_replaces_question_and_retrieval_metadata():
    tenant_id, project_id = "t_qa_edit", "p_qa_edit"
    async with SessionLocal() as s:
        qa = await KnowledgeService.create_qa(
            s, tenant_id, project_id, question="Where is the old handbook?",
            answer="On the old portal.", kind="legacy", tags=["old"],
        )
        updated = await KnowledgeService.update_qa(
            s, qa, question="Where is the employee handbook?",
            answer="Open People, then Documents.", kind="hr", tags=["people"],
        )
        hits = await KnowledgeService.top_qa(
            s, tenant_id, project_id, "employee handbook", top_k=1, threshold=0.0,
        )

    assert updated.question == "Where is the employee handbook?"
    assert updated.kind == "hr" and updated.tags == ["people"]
    assert hits and hits[0]["answer"] == "Open People, then Documents."
    assert hits[0]["kind"] == "hr"
