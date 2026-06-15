"""Long-term memory: remember + semantic recall, and the builtin tools."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.services.memory import MemoryService
from forge.services.runtime import make_runtime_ctx
from forge.tools.materialize import materialize_tool

T, P = "t_mem", "p_mem"


async def test_remember_then_recall():
    async with SessionLocal() as s:
        await MemoryService.remember(s, T, P, "The customer prefers email over phone.")
        await MemoryService.remember(s, T, P, "Our refund window is 30 days.")
    async with SessionLocal() as s:
        hits = await MemoryService.recall(s, T, P, "refund window days", top_k=5)
    assert any("30 days" in h for h in hits)  # the refund memory is recalled


async def test_scope_isolates_memories():
    async with SessionLocal() as s:
        await MemoryService.remember(s, "t_sc", "p_sc", "Alice's plan is enterprise.", scope="user:alice")
        await MemoryService.remember(s, "t_sc", "p_sc", "Bob's plan is free.", scope="user:bob")
    async with SessionLocal() as s:
        alice = await MemoryService.recall(s, "t_sc", "p_sc", "what plan", scope="user:alice", top_k=5)
    assert any("enterprise" in m for m in alice) and not any("free" in m for m in alice)


async def test_memory_builtin_tools():
    ctx = make_runtime_ctx(T, P)
    remember = materialize_tool({"name": "remember", "kind": "builtin", "builtin": "remember"}, ctx)
    recall = materialize_tool({"name": "recall", "kind": "builtin", "builtin": "recall"}, ctx)
    await remember.ainvoke({"text": "The launch date is March 2027."})
    out = await recall.ainvoke({"query": "when is the launch?"})
    assert "March 2027" in out
