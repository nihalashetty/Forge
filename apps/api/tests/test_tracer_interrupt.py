"""HITL interrupts are control flow, not failures.

When HumanInTheLoopMiddleware calls interrupt() it RAISES a GraphInterrupt (a GraphBubbleUp) to
suspend the graph for approval. That raised signal reaches the tracer's on_*_error callbacks, so
the tracer must recognize it and NOT record it as a span error - otherwise a run paused for human
approval renders as a crash (the agent + HITL spans showing red), which is exactly the misleading
trace this guards against. The run's own status is already a first-class `interrupted`.
"""

from __future__ import annotations

from langgraph.errors import GraphInterrupt

from forge.tracing.tracer import ForgeTracer


def test_chain_interrupt_is_not_a_span_error():
    tr = ForgeTracer()
    tr.on_chain_start({"name": "agent_1"}, {}, run_id="agent-1")
    tr.on_chain_error(GraphInterrupt(), run_id="agent-1")  # HITL pause bubbling up
    sp = tr.spans["agent-1"]
    assert sp.error is None, "an interrupt must not mark the span errored"
    assert sp.attributes.get("interrupted") is True
    assert sp.end is not None  # span is still closed (latency captured)


def test_tool_interrupt_is_not_a_span_error():
    tr = ForgeTracer()
    tr.on_tool_start({"name": "get_weather"}, "args", run_id="tool-1")
    tr.on_tool_error(GraphInterrupt(), run_id="tool-1")
    sp = tr.spans["tool-1"]
    assert sp.error is None
    assert sp.attributes.get("interrupted") is True


def test_real_error_is_still_recorded():
    tr = ForgeTracer()
    tr.on_chain_start({"name": "some_node"}, {}, run_id="node-1")
    tr.on_chain_error(ValueError("boom"), run_id="node-1")
    sp = tr.spans["node-1"]
    assert sp.error == "boom"
    assert not sp.attributes.get("interrupted")
