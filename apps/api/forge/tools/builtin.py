"""First-party builtin tools: current_time, calculator, web_fetch, web_search,
knowledge_search (agent-callable RAG over the project knowledge base)."""

from __future__ import annotations

import ast
import operator as op
from datetime import UTC, datetime

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# Safe arithmetic for the calculator (no names, no calls).
_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.Pow: op.pow, ast.Mod: op.mod, ast.USub: op.neg, ast.FloorDiv: op.floordiv,
}


def _calc(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc(node.left), _calc(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc(node.operand))
    raise ValueError("Unsupported expression")


class _CalcArgs(BaseModel):
    expression: str = Field(description="Arithmetic expression, e.g. '2 * (3 + 4)'")


class _TimeArgs(BaseModel):
    tz: str = Field(default="UTC", description="Timezone name (only UTC supported offline)")


class _FetchArgs(BaseModel):
    url: str = Field(description="URL to fetch")


def build_builtin_tool(cfg: dict, ctx):
    builtin = cfg["builtin"]
    name = cfg.get("name", builtin)
    desc = cfg.get("description", "")

    if builtin == "current_time":
        def now(tz: str = "UTC") -> str:
            return datetime.now(UTC).isoformat()
        return StructuredTool.from_function(func=now, name=name, description=desc or "Get the current UTC time.", args_schema=_TimeArgs)

    if builtin == "calculator":
        def calc(expression: str) -> str:
            return str(_calc(ast.parse(expression, mode="eval").body))
        return StructuredTool.from_function(func=calc, name=name, description=desc or "Evaluate an arithmetic expression.", args_schema=_CalcArgs)

    if builtin == "web_fetch":
        async def fetch(url: str) -> str:
            from forge.util.http import shared_async_client
            from forge.util.ssrf import guarded_get
            r = await guarded_get(
                shared_async_client(), url, policy=getattr(ctx, "egress_policy", None),
                timeout=20, follow_redirects=True,
            )
            return r.text[:8000]
        return StructuredTool.from_function(coroutine=fetch, name=name, description=desc or "Fetch a URL and return its text.", args_schema=_FetchArgs)

    if builtin == "web_search":  # requires a provider key (Tavily/Exa) — wired in Phase 7
        def search(query: str) -> str:
            return "web_search is not configured. Add a Tavily/Exa key to enable it."
        class _Q(BaseModel):
            query: str = Field(description="Search query")
        return StructuredTool.from_function(func=search, name=name, description=desc or "Search the web.", args_schema=_Q)

    if builtin == "knowledge_search":
        # Retrieval as a TOOL (vs the fixed `retrieval` node): the agent can search per
        # sub-question, multiple times, with its own phrasing — which is what makes a
        # single agent handle multi-part questions instead of a classifier→router
        # picking one path.
        class _KSearchArgs(BaseModel):
            query: str = Field(description="What to look up in the project knowledge base. Use one focused query per sub-question.")
            folder: str = Field(default="", description="Optional knowledge folder to search within (empty = all folders)")
            top_k: int = Field(default=4, description="How many document chunks to return")

        async def ksearch(query: str, folder: str = "", top_k: int = 4) -> str:
            from forge.db.base import SessionLocal
            from forge.services.knowledge import KnowledgeService

            async with SessionLocal() as s:
                embedder = await KnowledgeService.embedder_for_project(s, ctx.tenant_id, ctx.project_id)
                vec = await embedder.aembed_query(query)
                try:
                    hits = await KnowledgeService.search(
                        s, ctx.tenant_id, ctx.project_id, query, top_k=top_k,
                        folders=[folder] if folder else None, embedder=embedder, embedding=vec,
                    )
                except Exception:  # noqa: BLE001 - store empty / not ready
                    hits = []
                hits = [h for h in hits if h.score >= 0.18]
                try:
                    qa = await KnowledgeService.top_qa(
                        s, ctx.tenant_id, ctx.project_id, query, top_k=3, threshold=0.3,
                        embedder=embedder, embedding=vec,
                    )
                except Exception:  # noqa: BLE001
                    qa = []
            blocks = [f"[Doc {i + 1} · score {h.score:.2f}] {h.text}" for i, h in enumerate(hits)]
            blocks += [f"[FAQ] Q: {q['question']}\nA: {q['answer']}" for q in qa]
            return "\n\n".join(blocks) if blocks else "No relevant knowledge found for this query."

        return StructuredTool.from_function(
            coroutine=ksearch, name=name,
            description=desc or "Search the project knowledge base (documents + FAQs). Call once per distinct sub-question.",
            args_schema=_KSearchArgs,
        )

    if builtin == "remember":
        class _RememberArgs(BaseModel):
            text: str = Field(description="A concise fact worth remembering for future conversations (e.g. a preference, a decision, an account detail).")

        async def remember_tool(text: str) -> str:
            from forge.db.base import SessionLocal
            from forge.services.memory import MemoryService

            async with SessionLocal() as s:
                await MemoryService.remember(s, ctx.tenant_id, ctx.project_id, text)
            return "Saved to long-term memory."

        return StructuredTool.from_function(
            coroutine=remember_tool, name=name,
            description=desc or "Save a fact to long-term memory so it persists across conversations.",
            args_schema=_RememberArgs,
        )

    if builtin == "recall":
        class _RecallArgs(BaseModel):
            query: str = Field(description="What to look up in long-term memory.")

        async def recall_tool(query: str) -> str:
            from forge.db.base import SessionLocal
            from forge.services.memory import MemoryService

            async with SessionLocal() as s:
                mems = await MemoryService.recall(s, ctx.tenant_id, ctx.project_id, query, top_k=5)
            return "\n".join(f"- {m}" for m in mems) if mems else "No relevant memories found."

        return StructuredTool.from_function(
            coroutine=recall_tool, name=name,
            description=desc or "Recall previously remembered facts from long-term memory.",
            args_schema=_RecallArgs,
        )

    raise ValueError(f"Unknown builtin tool: {builtin!r}")


class _KbQuery(BaseModel):
    query: str = Field(description="A focused search query — use one per distinct sub-question, in your own words (not the user's whole message).")


def build_knowledge_capability_tools(knowledge: dict | None, ctx) -> list:
    """Built-in knowledge access attached straight to an agent node via its `knowledge`
    config — no separate Tool row needed. Two independent, separately-toggleable tools:

    - RAG  (`search_knowledge_base`): vector search over knowledge DOCUMENTS, scoped to the
      configured folders (empty = all).
    - Q&A  (`lookup_faq`): semantic match over curated FAQ / Q&A pairs, scoped to the
      configured kinds (empty = all).

    Unlike the fixed `retrieval` node (one search per run, before the agent), these are
    agent-driven: the agent decides when to search and rewrites the query per
    sub-question — which is what lets ONE agent answer multi-part questions.
    """
    tools: list = []
    if not knowledge:
        return tools
    rag = knowledge.get("rag") or {}
    qa = knowledge.get("qa") or {}

    if rag.get("enabled"):
        folders = rag.get("folders") or None
        top_k = int(rag.get("top_k") or 4)
        min_score = rag.get("min_score", 0.18)
        hybrid = bool(rag.get("hybrid", False))
        scope = f" (folders: {', '.join(folders)})" if folders else ""

        async def search_knowledge_base(query: str) -> str:
            from forge.db.base import SessionLocal
            from forge.services.knowledge import KnowledgeService

            async with SessionLocal() as s:
                try:
                    embedder = await KnowledgeService.embedder_for_project(s, ctx.tenant_id, ctx.project_id)
                    vec = await embedder.aembed_query(query)
                    hits = await KnowledgeService.search(
                        s, ctx.tenant_id, ctx.project_id, query, top_k=top_k,
                        folders=folders, embedder=embedder, embedding=vec, hybrid=hybrid,
                    )
                except Exception:  # noqa: BLE001 - store empty / not ready
                    hits = []
                hits = [h for h in hits if h.score >= min_score]
            blocks = [f"[Doc {i + 1} · score {h.score:.2f}] {h.text}" for i, h in enumerate(hits)]
            return "\n\n".join(blocks) if blocks else "No relevant documents found in the knowledge base for this query."

        tools.append(StructuredTool.from_function(
            coroutine=search_knowledge_base, name="search_knowledge_base",
            description=f"Search the project knowledge-base DOCUMENTS{scope} for grounding facts. Call once per distinct sub-question with a focused query.",
            args_schema=_KbQuery,
        ))

    if qa.get("enabled"):
        kinds = qa.get("kinds") or None
        threshold = float(qa.get("threshold", 0.3))
        top_k_qa = int(qa.get("top_k") or 3)
        scope = f" (kinds: {', '.join(kinds)})" if kinds else ""

        async def lookup_faq(query: str) -> str:
            from forge.db.base import SessionLocal
            from forge.services.knowledge import KnowledgeService

            async with SessionLocal() as s:
                try:
                    embedder = await KnowledgeService.embedder_for_project(s, ctx.tenant_id, ctx.project_id)
                    vec = await embedder.aembed_query(query)
                    qa_hits = await KnowledgeService.top_qa(
                        s, ctx.tenant_id, ctx.project_id, query, top_k=top_k_qa,
                        threshold=threshold, kinds=kinds, embedder=embedder, embedding=vec,
                    )
                except Exception:  # noqa: BLE001
                    qa_hits = []
            blocks = [f"[FAQ] Q: {q['question']}\nA: {q['answer']}" for q in qa_hits]
            return "\n\n".join(blocks) if blocks else "No matching FAQ / Q&A entry found for this query."

        tools.append(StructuredTool.from_function(
            coroutine=lookup_faq, name="lookup_faq",
            description=f"Look up curated FAQ / Q&A answers{scope}. Prefer these exact, approved answers when one matches the user's question.",
            args_schema=_KbQuery,
        ))

    return tools
