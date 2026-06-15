"""Subworkflow node: a parent workflow runs a referenced child as a reusable component."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from forge.engine.compiler import compile_workflow
from forge.services.runtime import make_runtime_ctx

_CHILD = {
    "id": "child", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:Child answered."}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}

_PARENT = {
    "id": "parent", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "start",
    "nodes": [
        {"id": "start", "type": "start", "config": {}},
        {"id": "sub", "type": "subworkflow", "config": {"workflow_id": "child_1"}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "start", "target": "sub"}, {"source": "sub", "target": "end"}],
}


async def test_subworkflow_runs_child():
    ctx = make_runtime_ctx("t_sub", "p_sub")
    ctx.checkpointer = InMemorySaver()
    ctx.workflows = {"child_1": _CHILD}
    graph = compile_workflow(_PARENT, ctx)
    out = await graph.ainvoke({"messages": [HumanMessage(content="hi")]}, {"configurable": {"thread_id": "s1"}})
    assert out["messages"][-1].content == "Child answered."


async def test_missing_subworkflow_is_passthrough():
    ctx = make_runtime_ctx("t_sub2", "p_sub2")
    ctx.checkpointer = InMemorySaver()
    ctx.workflows = {}  # referenced child not present
    graph = compile_workflow(_PARENT, ctx)
    out = await graph.ainvoke({"messages": [HumanMessage(content="hi")]}, {"configurable": {"thread_id": "s2"}})
    # no child -> passthrough; the original message survives, no crash
    assert out["messages"][-1].content == "hi"
