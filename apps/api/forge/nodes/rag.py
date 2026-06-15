"""Knowledge nodes: retrieval (RAG context injection) and qa_lookup (FAQ deflection).

Both read the latest user message from `messages` and write back into `messages`:
- retrieval appends a SystemMessage with the top-k chunks (grounding for a downstream agent).
- qa_lookup appends an AIMessage with the matched answer when similarity >= threshold.
"""

from __future__ import annotations

from typing import Any

from forge.engine.context import CompileContext
from forge.engine.registry import NodeSpec, Port, register

_KB_TAG = "forge_kb"


def _kb_removals(msgs: list[Any]) -> list[Any]:
    """RemoveMessage for every prior retrieval system-message (tagged), so KB context is
    EPHEMERAL — only the current turn's chunks stay in history instead of accumulating
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
    source_filter = cfg.get("source_filter") or None
    # Restrict retrieval to sources in these folders (resolved to source ids at run
    # time, so it composes with source_filter and needs no Chroma re-ingest).
    folders = cfg.get("folders") or None
    # Default a gentle floor so wildly off-topic queries surface no context (and the
    # grounded agent downstream refuses instead of answering from the closest chunk).
    min_score = cfg.get("min_score", 0.18)
    include_qa = cfg.get("include_qa", False)
    qa_threshold = cfg.get("qa_threshold", 0.3)
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
            # scoring (this node previously resolved the embedder + called the
            # embedding API twice per execution).
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
                ) if embedder else []
            except Exception:  # noqa: BLE001 - store not ready / empty
                hits = []
            if min_score is not None:
                hits = [h for h in hits if h.score >= min_score]
            for i, h in enumerate(hits):
                blocks.append(f"[Doc {i + 1}] {h.text}")
            if include_qa:
                try:
                    qa = await KnowledgeService.top_qa(
                        s, ctx.tenant_id, ctx.project_id, query, top_k=3, threshold=qa_threshold,
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


def qa_lookup_factory(cfg: dict, ctx: CompileContext):
    threshold = cfg.get("threshold", 0.85)
    # `kinds` (list, empty = all) is the canonical filter; single `kind` kept for
    # workflows saved before kinds existed.
    kinds = cfg.get("kinds") or None
    kind = cfg.get("kind", "any")
    # When set, also write "yes"/"no" to this state key so a downstream router can branch
    # on whether the FAQ deflected (enables the QA-first → RAG-fallback pattern). The
    # state field must be declared in the workflow's `state`. Optional + backward-compatible.
    route_key = cfg.get("route_key")

    async def _node(state: dict) -> dict:
        from forge.db.base import SessionLocal
        from forge.services.knowledge import KnowledgeService

        query = _last_user_text(state.get("messages") or [])
        if not query:
            return {route_key: "no"} if route_key else {}
        async with SessionLocal() as s:
            match = await KnowledgeService.lookup(s, ctx.tenant_id, ctx.project_id, query, threshold=threshold, kind=kind, kinds=kinds)
        out: dict[str, Any] = {}
        if match:
            from langchain_core.messages import AIMessage

            out["messages"] = [AIMessage(content=match["answer"])]
        if route_key:
            out[route_key] = "yes" if match else "no"
        return out

    return _node


register(NodeSpec(
    type="retrieval", schema_id="forge/nodes/retrieval",
    input_ports=[Port(id="in", io_type="text", direction="in")],
    output_ports=[Port(id="out", io_type="json", direction="out")],
    factory=retrieval_factory, category="knowledge", label="Retrieval", description="RAG query",
    summarize=lambda c: [f"top_k {c.get('top_k', 5)}" + (" · hybrid" if c.get("hybrid") else "")],
))
register(NodeSpec(
    type="qa_lookup", schema_id="forge/nodes/qa_lookup",
    input_ports=[Port(id="in", io_type="text", direction="in")],
    output_ports=[Port(id="out", io_type="text", direction="out")],
    factory=qa_lookup_factory, category="knowledge", label="Q&A Lookup", description="Semantic pair match",
    summarize=lambda c: [
        f"threshold ≥ {c.get('threshold', 0.85)}",
        "kinds · " + (", ".join(c.get("kinds") or []) or c.get("kind", "any") or "any"),
    ],
))
