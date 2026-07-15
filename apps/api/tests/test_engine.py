"""End-to-end validation of the Forge engine, fully offline (fake model).

Proves: state TypedDict + reducers, the node registry, the workflow compiler,
router expression routing, middleware attachment, and an actual graph run.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from forge.engine.compiler import compile_workflow
from forge.engine.context import CompileContext
from forge.engine.expressions import ExpressionError, eval_expression
from forge.engine.state import build_state_typeddict
from forge.tools.projection import estimate_tokens, project_response


def _ctx() -> CompileContext:
    return CompileContext(tenant_id="t1", project_id="p1", checkpointer=InMemorySaver())


def _wf() -> dict:
    return {
        "id": "wf_test",
        "version": 1,
        "state": {
            "messages": {"type": "list[message]", "reducer": "add_messages"},
            "intent": {"type": "str", "reducer": "last"},
        },
        "entry_node": "start",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "route",
                "type": "router",
                "config": {
                    "expression": "intent",
                    "cases": {"billing": "billing_agent", "tech": "tech_agent"},
                    "default": "billing_agent",
                },
            },
            {
                "id": "billing_agent",
                "type": "agent",
                "config": {
                    "flavor": "agent",
                    "model": "fake:Billing handled.",
                    "system_prompt": "You are the billing agent.",
                    "middleware": [
                        {"type": "model_call_limit", "config": {"run_limit": 3}},
                        {"type": "summarization", "config": {"trigger": ["tokens", 4000]}},
                    ],
                },
            },
            {
                "id": "tech_agent",
                "type": "agent",
                "config": {"flavor": "agent", "model": "fake:Tech handled."},
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "route"},
            {"source": "billing_agent", "target": "end"},
            {"source": "tech_agent", "target": "end"},
        ],
    }


# --- state builder --------------------------------------------------------


def test_build_state_typeddict_injects_messages_and_reducers():
    State = build_state_typeddict({"findings": {"type": "list[str]", "reducer": "add"}})
    ann = State.__annotations__
    assert "messages" in ann  # auto-injected
    assert "findings" in ann


async def test_add_reducer_accumulates_across_nodes():
    State = build_state_typeddict(
        {"items": {"type": "list[str]", "reducer": "add"}, "intent": {"type": "str", "reducer": "last"}}
    )
    g = StateGraph(State)
    g.add_node("a", lambda s: {"items": ["a"], "intent": "x"})
    g.add_node("b", lambda s: {"items": ["b"], "intent": "y"})
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.set_finish_point("b")
    out = await g.compile().ainvoke({})
    assert out["items"] == ["a", "b"]  # accumulated via operator.add
    assert out["intent"] == "y"  # overwritten via "last"


# --- expressions ----------------------------------------------------------


def test_expression_sandbox_evaluates_and_blocks_imports():
    assert eval_expression("intent == 'billing'", {"intent": "billing"}) is True
    assert eval_expression("len(messages) > 1", {"messages": [1, 2, 3]}) is True
    with pytest.raises(ExpressionError):
        eval_expression("__import__('os').system('echo hi')", {})


# --- projection (token lever) --------------------------------------------


def test_projection_jmespath_then_fields_then_full():
    raw = {"data": {"totals": {"subtotal": 90, "grand_total": 99}, "line_items": [1, 2, 3, 4]}}
    jm = project_response(raw, {"projection_jmespath": "data.totals.{sub: subtotal, total: grand_total}"})
    assert jm == {"sub": 90, "total": 99}

    fld = project_response(
        raw,
        {"fields": [
            {"path": "data.totals.subtotal", "include_in_llm": True},
            {"path": "data.line_items", "include_in_llm": False},
        ]},
    )
    assert fld == {"data.totals.subtotal": 90}

    assert project_response(raw, {}) == raw
    assert estimate_tokens(raw) > estimate_tokens(jm)  # the meter shrinks


# --- compile + run --------------------------------------------------------


@pytest.mark.parametrize(
    "intent,expected",
    [("billing", "Billing handled."), ("tech", "Tech handled."), ("other", "Billing handled.")],
)
async def test_compile_and_run_routes_correctly(intent, expected):
    graph = compile_workflow(_wf(), _ctx())
    config = {"configurable": {"thread_id": f"thread-{intent}"}}
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="hi")], "intent": intent}, config
    )
    last = out["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.content == expected


async def test_human_input_resume_can_drive_router_branch():
    wf = {
        "id": "wf_hitl_router",
        "version": 1,
        "state": {
            "messages": {"type": "list[message]", "reducer": "add_messages"},
            "decision": {"type": "str", "reducer": "last"},
            "result": {"type": "str", "reducer": "last"},
        },
        "entry_node": "start",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "review",
                "type": "human_input",
                "config": {
                    "prompt": "Approve?",
                    "allowed_decisions": ["approve", "reject"],
                    "output_key": "decision",
                },
            },
            {
                "id": "route",
                "type": "router",
                "config": {
                    "expression": "decision",
                    "cases": {"approve": "approved", "reject": "rejected"},
                    "default": "rejected",
                },
            },
            {"id": "approved", "type": "transform", "config": {"expression": "'approved'", "output_key": "result"}},
            {"id": "rejected", "type": "transform", "config": {"expression": "'rejected'", "output_key": "result"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "review"},
            {"source": "review", "target": "route"},
            {"source": "approved", "target": "end"},
            {"source": "rejected", "target": "end"},
        ],
    }
    graph = compile_workflow(wf, _ctx())
    config = {"configurable": {"thread_id": "hitl-router"}}

    first = await graph.ainvoke({"messages": [HumanMessage(content="needs review")]}, config)
    assert "__interrupt__" in first

    out = await graph.ainvoke(Command(resume="approve"), config)
    assert out["decision"] == "approve"
    assert out["result"] == "approved"
