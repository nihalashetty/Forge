"""parallel_fanout (Send map) + join aggregation, and the loop node."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from forge.engine.compiler import compile_workflow
from forge.engine.context import CompileContext
from forge.nodes.flow import loop_factory
from forge.services.runtime import make_runtime_ctx

_FANOUT_WF = {
    "id": "fan", "version": 1,
    "state": {
        "messages": {"type": "list[message]", "reducer": "add_messages"},
        "items": {"type": "list[json]", "reducer": "last"},
        "results": {"type": "list[str]", "reducer": "add"},
    },
    "entry_node": "start",
    "nodes": [
        {"id": "start", "type": "start", "config": {}},
        {"id": "fan", "type": "parallel_fanout", "config": {"over": "items", "child_node": "worker", "item_key": "item"}},
        {"id": "worker", "type": "transform", "config": {"expression": "[item]", "output_key": "results"}},
        {"id": "join", "type": "join", "config": {"reducer": "concat"}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [
        {"source": "start", "target": "fan"},
        {"source": "worker", "target": "join"},
        {"source": "join", "target": "end"},
    ],
}


async def test_parallel_fanout_maps_over_items():
    ctx = make_runtime_ctx("t_fan", "p_fan")
    ctx.checkpointer = InMemorySaver()
    graph = compile_workflow(_FANOUT_WF, ctx)
    out = await graph.ainvoke({"items": ["a", "b", "c"]}, {"configurable": {"thread_id": "f1"}})
    assert sorted(out["results"]) == ["a", "b", "c"]  # every item processed in parallel + aggregated


def test_loop_node_counts_and_stops():
    node = loop_factory({"max_iter": 3, "condition": ""}, CompileContext(tenant_id="t", project_id="p"))
    s1 = node({"_loop_count": 0})
    assert s1 == {"_loop_count": 1, "_loop": "continue"}
    s2 = node({"_loop_count": 2})
    assert s2 == {"_loop_count": 3, "_loop": "done"}  # hit max_iter


def test_loop_condition_stops_early():
    node = loop_factory({"max_iter": 10, "condition": "keep_going == True"}, CompileContext(tenant_id="t", project_id="p"))
    assert node({"_loop_count": 0, "keep_going": True})["_loop"] == "continue"
    assert node({"_loop_count": 0, "keep_going": False})["_loop"] == "done"
