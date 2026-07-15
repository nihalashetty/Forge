"""Knowledge node: retrieval - RAG document search + curated Q&A lookup in one node.

It reads the latest user message from `messages` and appends a SystemMessage with the
retrieved document chunks and/or matching Q&A pairs (grounding for a downstream agent).
Document search (include_docs) and Q&A lookup (include_qa) are toggled independently.
"""

from __future__ import annotations

from typing import Any

from forge.engine.context import CompileContext
from forge.engine.registry import NodeSpec, Port, register
from forge.knowledge.embeddings import DEFAULT_MIN_SCORE, DEFAULT_RERANK_MIN_SCORE
from forge.knowledge.store import citation_for

_KB_TAG = "forge_kb"


def _passes_floor(h: Any, min_score: float, hybrid: bool) -> bool:
    """Cosine-floor a hit on the RIGHT scale. In hybrid mode Hit.score is the fused RANK (top≈1.0),
    NOT cosine, so threshold Hit.vector_score (the real cosine) instead; a BM25-only hit has no
    cosine (vector_score None) and is kept (a strong exact-term match shouldn't be floored out).
    In vector-only mode Hit.score IS the cosine."""
    cos = h.vector_score if hybrid else h.score
    return cos is None or cos >= min_score


def _kb_removals(msgs: list[Any]) -> list[Any]:
    """RemoveMessage for every prior retrieval system-message (tagged), so KB context is
    EPHEMERAL - only the current turn's chunks stay in history instead of accumulating
    (which otherwise grows cost every turn on a checkpointed thread)."""
    from langchain_core.messages import RemoveMessage

    out: list[Any] = []
    for m in msgs or []:
        ak = m.get("additional_kwargs") if isinstance(m, dict) else (getattr(m, "additional_kwargs", {}) or {})
        if ak and ak.get(_KB_TAG):
            mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", None)
            if mid:
                out.append(RemoveMessage(id=mid))
    return out


def _last_user_text(msgs: list[Any]) -> str:
    for m in reversed(msgs or []):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", None)
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if role in ("human", "user", None) and content:
            return content if isinstance(content, str) else str(content)
    if msgs:
        m = msgs[-1]
        c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        return c if isinstance(c, str) else str(c or "")
    return ""


def retrieval_factory(cfg: dict, ctx: CompileContext):
    top_k = cfg.get("top_k", 5)
    # RAG document search is on by default. Turn it off for a Q&A-only retrieval node:
    # include_docs=False + include_qa=True makes this node subsume the old qa_lookup.
    include_docs = cfg.get("include_docs", True)
    # Hybrid = fuse BM25 lexical ranking with vector search (RRF). Opt-in; default vector-only.
    hybrid = bool(cfg.get("hybrid", False))
    # Rerank = a second-stage local cross-encoder over a larger shortlist, keeping the best
    # top_k. Opt-in; big accuracy win at some latency. rerank_top_n sizes the shortlist.
    rerank = bool(cfg.get("rerank", False))
    rerank_top_n = cfg.get("rerank_top_n")
    source_filter = cfg.get("source_filter") or None
    # Restrict retrieval to sources in these folders (resolved to source ids at run
    # time, so it composes with source_filter and needs no Chroma re-ingest).
    folders = cfg.get("folders") or None
    # Cosine floor so wildly off-topic queries surface no context (the grounded agent then refuses
    # instead of answering from the nearest chunk). Calibrated to the default BGE embedder (~0.6;
    # related pairs ~0.75+, unrelated ~0.4-0.5) - lower it for a hosted model on a smaller scale.
    min_score = cfg.get("min_score", DEFAULT_MIN_SCORE)
    # When rerank is on, Hit.score is the cross-encoder sigmoid (a DIFFERENT scale from cosine), so
    # min_score can't apply; this separate floor lets an off-topic reranked query still yield empty.
    rerank_min_score = cfg.get("rerank_min_score", DEFAULT_RERANK_MIN_SCORE)
    # Optional MMR diversity pass (trade a little relevance for less-redundant top_k).
    mmr = bool(cfg.get("mmr", False))
    mmr_lambda = cfg.get("mmr_lambda", 0.5)
    include_qa = cfg.get("include_qa", False)
    qa_threshold = cfg.get("qa_threshold", 0.3)
    qa_top_k = cfg.get("qa_top_k", 3)
    # Only include Q&A pairs of these kinds/categories (empty = all kinds).
    qa_kinds = cfg.get("qa_kinds") or None
    # When nothing relevant is found, inject an explicit note so a grounded agent knows
    # to say it doesn't have the answer rather than fall back to model world-knowledge.
    announce_empty = cfg.get("announce_empty", False)
    # When set, also write "yes"/"no" (found anything?) to this state key so a downstream
    # router can branch found → agent / not-found → escalation. Optional; key must be
    # declared in workflow state (the canvas auto-declares node-written keys).
    route_key = cfg.get("route_key")

    async def _node(state: dict) -> dict:
        from langchain_core.messages import SystemMessage

        from forge.db.base import SessionLocal
        from forge.services.knowledge import KnowledgeService

        query = _last_user_text(state.get("messages") or [])
        if not query:
            return {route_key: "no"} if route_key else {}

        blocks: list[str] = []
        async with SessionLocal() as s:
            # Embed the query ONCE and reuse the vector for both doc search and Q&A
            # scoring. Skip entirely when neither source is enabled, so a node configured
            # to do nothing makes no embedding API call.
            embedder = qvec = None
            if include_docs or include_qa:
                try:
                    embedder = await KnowledgeService.embedder_for_project(s, ctx.tenant_id, ctx.project_id)
                    qvec = await embedder.aembed_query(query)
                except Exception as e:  # noqa: BLE001 - embedder unavailable
                    from forge.util.metrics import incr

                    incr("retrieval.embedder_unavailable", detail=str(e))
                    embedder = qvec = None
            try:
                hits = await KnowledgeService.search(
                    s, ctx.tenant_id, ctx.project_id, query, top_k=top_k,
                    source_ids=source_filter, folders=folders, embedder=embedder, embedding=qvec,
                    hybrid=hybrid, rerank=rerank, rerank_top_n=rerank_top_n, mmr=mmr, mmr_lambda=mmr_lambda,
                ) if (include_docs and embedder) else []
            except Exception:  # noqa: BLE001 - store not ready / empty
                hits = []
            # Apply the relevance floor on the correct scale. Reranked hits use the cross-encoder
            # sigmoid floor (rerank_min_score); otherwise the cosine floor (min_score) on the real
            # cosine - Hit.vector_score in hybrid mode (Hit.score there is the fused rank, not cosine).
            if rerank:
                if rerank_min_score is not None:
                    hits = [h for h in hits if h.score >= rerank_min_score]
            elif min_score is not None:
                hits = [h for h in hits if _passes_floor(h, min_score, hybrid)]
            for i, h in enumerate(hits):
                cite = citation_for(h.metadata)
                label = f"Doc {i + 1} · {cite}" if cite else f"Doc {i + 1}"
                blocks.append(f"[{label}] {h.text}")
            if include_qa:
                try:
                    qa = await KnowledgeService.top_qa(
                        s, ctx.tenant_id, ctx.project_id, query, top_k=qa_top_k, threshold=qa_threshold,
                        kinds=qa_kinds, embedder=embedder, embedding=qvec,
                    )
                except Exception:  # noqa: BLE001
                    qa = []
                for q in qa:
                    blocks.append(f"[FAQ] Q: {q['question']}\nA: {q['answer']}")

        out: dict[str, Any] = {}
        if route_key:
            out[route_key] = "yes" if blocks else "no"
        removals = _kb_removals(state.get("messages") or [])
        if not blocks:
            if announce_empty:
                out["messages"] = [*removals, SystemMessage(
                    content="KNOWLEDGE BASE: no relevant entries were found for the user's question.",
                    additional_kwargs={_KB_TAG: True},
                )]
            elif removals:
                out["messages"] = removals
            return out

        ctxt = "\n\n".join(blocks)
        out["messages"] = [*removals, SystemMessage(
            content="KNOWLEDGE BASE context for the user's question:\n" + ctxt,
            additional_kwargs={_KB_TAG: True},
        )]
        return out

    return _node


def _retrieval_summary(c: dict) -> list[str]:
    """Glanceable canvas lines: which sources this retrieval node pulls from."""
    lines: list[str] = []
    if c.get("include_docs", True):
        flags = ("" if not c.get("hybrid") else " · hybrid") + ("" if not c.get("rerank") else " · rerank")
        lines.append(f"docs top_k {c.get('top_k', 5)}{flags}")
    if c.get("include_qa"):
        lines.append(f"Q&A top_k {c.get('qa_top_k', 3)}")
    return lines or ["no sources enabled"]


register(NodeSpec(
    type="retrieval", schema_id="forge/nodes/retrieval",
    input_ports=[Port(id="in", io_type="text", direction="in")],
    output_ports=[Port(id="out", io_type="json", direction="out")],
    factory=retrieval_factory, category="knowledge", label="Retrieval",
    description="RAG document search + Q&A lookup",
    summarize=_retrieval_summary,
))
