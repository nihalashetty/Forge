"""Regression tests for the "ghost config" audit fixes - node/middleware schema options that
were exposed in the UI but silently ignored by the compiler, plus the new validation rules.

Everything here is engine-only (compile_workflow / validate_workflow with fake models and an
InMemorySaver), so no database or network is needed.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp

from forge.engine.compiler import compile_workflow
from forge.engine.context import CompileContext
from forge.nodes.flow import FANOUT_INDEX_KEY, join_factory, resilient_fanout_child
from forge.services.validation import validate_workflow


def _ctx() -> CompileContext:
    return CompileContext(tenant_id="t1", project_id="p1", checkpointer=InMemorySaver())


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


# --------------------------------------------------------------------------------------------
# Finding 1: join `reducer` is honored (was a pure passthrough).
# --------------------------------------------------------------------------------------------

def test_join_reducer_merge_first_last_concat():
    ctx = _ctx()
    assert join_factory({"reducer": "merge", "input_key": "p", "output_key": "o"}, ctx)(
        {"p": [{"a": 1}, {"b": 2}]}
    ) == {"o": {"a": 1, "b": 2}}
    assert join_factory({"reducer": "last", "input_key": "p", "output_key": "o"}, ctx)(
        {"p": [1, 2, 3]}
    ) == {"o": 3}
    assert join_factory({"reducer": "first", "input_key": "p", "output_key": "o"}, ctx)(
        {"p": [1, 2, 3]}
    ) == {"o": 1}
    assert join_factory({"reducer": "concat", "input_key": "p", "output_key": "o"}, ctx)(
        {"p": [[1], [2, 3]]}
    ) == {"o": [1, 2, 3]}


def test_join_without_input_key_is_passthrough_marker():
    # No input_key -> convergence marker (aggregation stays with the state-key reducer).
    assert join_factory({"reducer": "concat"}, _ctx())({"anything": 1}) == {}


# --------------------------------------------------------------------------------------------
# Finding 2: parallel_fanout index tagging + partial-failure isolation + per-item timeout.
# --------------------------------------------------------------------------------------------

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


async def test_fanout_still_maps_and_index_tag_does_not_leak():
    graph = compile_workflow(_FANOUT_WF, _ctx())
    out = await graph.ainvoke({"items": ["a", "b", "c"]}, _cfg("fan-1"))
    assert sorted(out["results"]) == ["a", "b", "c"]
    # The index/total ride only in the child's Send payload; they must not leak to run state.
    assert FANOUT_INDEX_KEY not in out and "_fanout_total" not in out


async def test_fanout_continue_on_error_still_runs():
    graph = compile_workflow({**_FANOUT_WF, "error_policy": "continue"}, _ctx())
    out = await graph.ainvoke({"items": ["x", "y"]}, _cfg("fan-2"))
    assert sorted(out["results"]) == ["x", "y"]


async def test_resilient_child_isolates_one_failure_but_keeps_the_rest():
    def child(state):
        if state.get(FANOUT_INDEX_KEY) == 1:
            raise ValueError("boom")
        return {"results": [state["item"]]}

    skip = resilient_fanout_child(child, isolate=True)
    assert await skip({"item": "a", FANOUT_INDEX_KEY: 0}) == {"results": ["a"]}
    assert await skip({"item": "b", FANOUT_INDEX_KEY: 1}) == {}  # failure isolated

    fail = resilient_fanout_child(child, isolate=False)
    with pytest.raises(ValueError):
        await fail({"item": "b", FANOUT_INDEX_KEY: 1})


async def test_resilient_child_propagates_control_flow_and_honors_timeout():
    async def bubbles(state):
        raise GraphBubbleUp()  # interrupts / Command bubbling must NOT be swallowed

    with pytest.raises(GraphBubbleUp):
        await resilient_fanout_child(bubbles, isolate=True)({FANOUT_INDEX_KEY: 0})

    async def slow(state):
        await asyncio.sleep(1)
        return {"results": ["late"]}

    assert await resilient_fanout_child(slow, timeout=0.05, isolate=True)({FANOUT_INDEX_KEY: 0}) == {}


# --------------------------------------------------------------------------------------------
# Finding 3: tenant_budget honors max_usd_per_thread and scopes tokens to the run.
# --------------------------------------------------------------------------------------------

def test_tenant_budget_run_scoped_tokens():
    from forge.engine.middleware_compiler import _tenant_budget

    mw = _tenant_budget({"max_tokens_per_run": 10, "on_exceed": "end"}, None)
    msg = AIMessage(content="x", usage_metadata={"input_tokens": 6, "output_tokens": 6, "total_tokens": 12})
    assert mw.after_model({"messages": [msg]})["_forge_run_tokens"] == 12
    stop = mw.before_model({"_forge_run_tokens": 12})
    assert stop and stop.get("jump_to") == "end"
    assert mw.before_model({"_forge_run_tokens": 0}) is None


def test_tenant_budget_usd_accounting():
    from forge.engine.middleware_compiler import _tenant_budget

    mw = _tenant_budget({"max_usd_per_thread": 1.0, "on_exceed": "error"}, None)
    # gpt-4.1-mini input is $0.40/1M tokens -> 1M input tokens == $0.40.
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000},
        response_metadata={"model_name": "gpt-4.1-mini"},
    )
    upd = mw.after_model({"messages": [msg]})
    assert abs(upd["_forge_thread_cost_usd"] - 0.4) < 1e-6
    with pytest.raises(RuntimeError):
        mw.before_model({"_forge_thread_cost_usd": 2.0})


# --------------------------------------------------------------------------------------------
# Finding 4: guardrail_regex honors apply_to and implements redact/flag (block still replaces).
# --------------------------------------------------------------------------------------------

def test_guardrail_redact_masks_input_and_output():
    from forge.engine.middleware_compiler import _guardrail_regex

    mw = _guardrail_regex({"patterns": ["forbidden"], "on_match": "redact", "apply_to": "both"}, None)
    out = mw.after_model({"messages": [AIMessage(content="the forbidden secret", id="a1")]})
    assert "[redacted]" in out["messages"][-1].content and "forbidden" not in out["messages"][-1].content
    inp = mw.before_model({"messages": [HumanMessage(content="my forbidden input", id="h1")]})
    assert "[redacted]" in inp["messages"][-1].content


def test_guardrail_flag_marks_without_changing_content():
    from forge.engine.middleware_compiler import _guardrail_regex

    mw = _guardrail_regex({"patterns": ["bad"], "on_match": "flag", "apply_to": "output"}, None)
    out = mw.after_model({"messages": [AIMessage(content="this is bad", id="a2")]})
    assert out["messages"][-1].additional_kwargs.get("guardrail_flagged")
    assert out["messages"][-1].content == "this is bad"


def test_guardrail_output_only_ignores_input():
    from forge.engine.middleware_compiler import _guardrail_regex

    mw = _guardrail_regex({"patterns": ["forbidden"], "on_match": "block", "apply_to": "output"}, None)
    assert mw.before_model({"messages": [HumanMessage(content="forbidden", id="h9")]}) is None


async def test_guardrail_block_still_replaces_reply():
    wf = {
        "id": "g", "version": 1,
        "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
        "entry_node": "start",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "agent", "type": "agent",
             "config": {"flavor": "agent", "model": "fake:the forbidden secret",
                        "middleware": [{"type": "guardrail_regex",
                                        "config": {"patterns": ["forbidden"], "on_match": "block"}}]}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "agent"}, {"source": "agent", "target": "end"}],
    }
    out = await compile_workflow(wf, _ctx()).ainvoke({"messages": [HumanMessage(content="hi")]}, _cfg("g1"))
    texts = [getattr(m, "content", "") for m in out["messages"]]
    assert any("[blocked by content guardrail]" in t for t in texts)
    assert not any("forbidden secret" in t for t in texts)


# --------------------------------------------------------------------------------------------
# Finding 5: model_retry passes retry_on through.
# --------------------------------------------------------------------------------------------

def test_model_retry_passes_retry_on():
    from forge.engine.middleware_compiler import _model_retry

    mw = _model_retry({"max_retries": 1, "retry_on": ["timeout", "http_error"]}, None)
    assert TimeoutError in mw.retry_on and httpx.HTTPError in mw.retry_on
    assert _model_retry({"max_retries": 1}, None).retry_on == (Exception,)


# --------------------------------------------------------------------------------------------
# Advanced middleware are async-safe now (were sync-only -> crashed under ainvoke).
# --------------------------------------------------------------------------------------------

async def test_dynamic_model_by_state_middleware_runs_async():
    # The advanced middleware were sync-only and raised NotImplementedError under ainvoke/astream
    # (the real runtime path). They must now run async AND actually apply the model override.
    # Rules evaluate against the AGENT's visible state; switching on an arbitrary parent-workflow
    # state key is a separate, documented limitation (the agent subgraph boundary doesn't forward
    # it), so this asserts the mechanism over a rule the agent can evaluate.
    def wf(rule_when: str) -> dict:
        return {
            "id": "dm", "version": 1,
            "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
            "entry_node": "start",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {"id": "agent", "type": "agent", "config": {
                    "flavor": "agent", "model": "fake:base",
                    "middleware": [{"type": "dynamic_model_by_state",
                                    "config": {"rules": [{"when": rule_when, "use": "fake:switched"}], "default": "fake:base"}}],
                }},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [{"source": "start", "target": "agent"}, {"source": "agent", "target": "end"}],
        }
    matched = await compile_workflow(wf("True"), _ctx()).ainvoke({"messages": [HumanMessage(content="hi")]}, _cfg("dm1"))
    assert any("switched" in getattr(m, "content", "") for m in matched["messages"])
    default = await compile_workflow(wf("False"), _ctx()).ainvoke({"messages": [HumanMessage(content="hi")]}, _cfg("dm2"))
    assert any("base" in getattr(m, "content", "") for m in default["messages"])


# --------------------------------------------------------------------------------------------
# Finding 6: subworkflow input_mapping/output_mapping remap parent<->child keys.
# --------------------------------------------------------------------------------------------

_CHILD = {
    "id": "child", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"},
              "child_in": {"type": "str", "reducer": "last"},
              "child_out": {"type": "str", "reducer": "last"}},
    "entry_node": "t",
    "nodes": [
        {"id": "t", "type": "transform", "config": {"expression": "child_in", "output_key": "child_out"}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "t", "target": "end"}],
}


async def test_subworkflow_input_output_mapping():
    parent = {
        "id": "parent", "version": 1,
        "state": {"messages": {"type": "list[message]", "reducer": "add_messages"},
                  "p_val": {"type": "str", "reducer": "last"},
                  "p_result": {"type": "str", "reducer": "last"}},
        "entry_node": "start",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "sub", "type": "subworkflow", "config": {
                "workflow_id": "child_1",
                "input_mapping": {"p_val": "child_in"},
                "output_mapping": {"child_out": "p_result"}}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "sub"}, {"source": "sub", "target": "end"}],
    }
    ctx = _ctx()
    ctx.workflows = {"child_1": _CHILD}
    out = await compile_workflow(parent, ctx).ainvoke({"p_val": "hello"}, _cfg("sub-map"))
    assert out.get("p_result") == "hello"


# --------------------------------------------------------------------------------------------
# Finding 7: transform engine=jq raises clearly when jq missing; jmespath errors -> None.
# --------------------------------------------------------------------------------------------

def test_transform_jq_raises_when_unavailable():
    from forge.nodes.data import transform_factory

    node = transform_factory({"engine": "jq", "expression": ".x", "output_key": "data"}, _ctx())
    with pytest.raises(ValueError, match="jq"):
        node({"x": 1})


def test_transform_bad_jmespath_returns_none():
    from forge.nodes.data import transform_factory

    node = transform_factory({"expression": "foo[", "output_key": "data"}, _ctx())
    assert node({"foo": 1}) == {"data": None}


# --------------------------------------------------------------------------------------------
# Finding 10: new validation rules (+ the pre-existing fanout-adjacency false positive).
# --------------------------------------------------------------------------------------------

def _wf_with(nodes, edges, state=None):
    return {
        "id": "v", "version": 1,
        "state": state or {"messages": {"type": "list[message]", "reducer": "add_messages"}},
        "entry_node": "start", "nodes": nodes, "edges": edges,
    }


def test_fanout_workflow_validates_cleanly():
    # Pre-existing bug: the validator didn't model parallel_fanout -> child, so it wrongly
    # reported worker/join/end unreachable + "no path to END". It should validate now.
    res = validate_workflow(_FANOUT_WF)
    assert res.valid, res.errors


def test_undeclared_write_is_an_error():
    wf = _wf_with(
        [
            {"id": "start", "type": "start", "config": {}},
            {"id": "x", "type": "transform", "config": {"expression": "`1`", "output_key": "ghost_key"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        [{"source": "start", "target": "x"}, {"source": "x", "target": "end"}],
    )
    res = validate_workflow(wf)
    assert not res.valid
    assert any("ghost_key" in e["message"] for e in res.errors)
    # Declaring it clears the error.
    wf2 = _wf_with(
        wf["nodes"], wf["edges"],
        state={"messages": {"type": "list[message]", "reducer": "add_messages"},
               "ghost_key": {"type": "json", "reducer": "last"}},
    )
    assert validate_workflow(wf2).valid


def test_reachable_dead_end_warns():
    wf = _wf_with(
        [
            {"id": "start", "type": "start", "config": {}},
            {"id": "a", "type": "agent", "config": {"flavor": "agent", "model": "fake:x"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        # 'a' is reachable but has no outgoing edge; start also reaches end so the graph is valid.
        [{"source": "start", "target": "a"}, {"source": "start", "target": "end"}],
    )
    res = validate_workflow(wf)
    assert any("no outgoing edge" in w["message"] and w.get("node_id") == "a" for w in res.warnings)


def test_branches_edge_requires_condition():
    wf = _wf_with(
        [
            {"id": "start", "type": "start", "config": {}},
            {"id": "a", "type": "agent", "config": {"flavor": "agent", "model": "fake:x"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        [
            {"source": "start", "target": "a"},
            {"source": "a", "target": "end", "branches": {"yes": "end"}},  # missing condition
        ],
    )
    res = validate_workflow(wf)
    assert not res.valid
    assert any("no condition" in e["message"] for e in res.errors)


# --------------------------------------------------------------------------------------------
# Finding 8: a branches edge routes on its condition, and an unmatched value ends the run
# gracefully (END is a valid target) instead of raising KeyError('__end__') at runtime.
# --------------------------------------------------------------------------------------------

def _branch_wf():
    return {
        "id": "br", "version": 1,
        "state": {"messages": {"type": "list[message]", "reducer": "add_messages"},
                  "intent": {"type": "str", "reducer": "last"}},
        "entry_node": "start",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "gate", "type": "transform", "config": {"expression": "intent", "output_key": "intent"}},
            {"id": "a", "type": "agent", "config": {"flavor": "agent", "model": "fake:A-ANSWER"}},
            {"id": "b", "type": "agent", "config": {"flavor": "agent", "model": "fake:B-ANSWER"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "gate"},
            {"source": "gate", "target": "end", "condition": "intent", "branches": {"x": "a", "y": "b"}},
            {"source": "a", "target": "end"},
            {"source": "b", "target": "end"},
        ],
    }


async def test_branch_edge_routes_and_ends_gracefully_on_no_match():
    graph = compile_workflow(_branch_wf(), _ctx())
    out = await graph.ainvoke({"messages": [HumanMessage(content="hi")], "intent": "y"}, _cfg("br-y"))
    texts = [getattr(m, "content", "") for m in out["messages"]]
    assert any("B-ANSWER" in t for t in texts) and not any("A-ANSWER" in t for t in texts)
    # Unmatched value must route to END without crashing (was KeyError('__end__')).
    out2 = await graph.ainvoke({"messages": [HumanMessage(content="hi")], "intent": "z"}, _cfg("br-z"))
    assert not any("ANSWER" in getattr(m, "content", "") for m in out2["messages"])


# --------------------------------------------------------------------------------------------
# Finding 9: unwired agent fields (memory/filesystem/permissions) surface as warnings.
# --------------------------------------------------------------------------------------------

def test_unwired_agent_fields_warn():
    wf = _wf_with(
        [
            {"id": "start", "type": "start", "config": {}},
            {"id": "a", "type": "agent", "config": {
                "flavor": "agent", "model": "fake:x",
                "permissions": [{"path": "/x", "access": "read"}],
                "memory": {"long_term": True}}},
            {"id": "end", "type": "end", "config": {}},
        ],
        [{"source": "start", "target": "a"}, {"source": "a", "target": "end"}],
    )
    res = validate_workflow(wf)
    assert res.valid  # warnings only, never block save
    assert any("permissions" in w["message"] for w in res.warnings)
    assert any("memory" in w["message"].lower() for w in res.warnings)


# --------------------------------------------------------------------------------------------
# Library drift: langchain-openai (>=1.3) renamed OpenAIModerationMiddleware's apply_to_* flags
# to check_*. Enabling `openai_moderation` used to crash at compile with
# "__init__() got an unexpected keyword argument 'apply_to_input'"; the compiler now translates.
# --------------------------------------------------------------------------------------------

def test_openai_moderation_translates_apply_to_flags():
    pytest.importorskip("langchain_openai")
    from forge.engine.middleware_compiler import _openai_moderation

    mw = _openai_moderation({"apply_to_input": True, "apply_to_output": False}, None)
    assert mw.check_input is True and mw.check_output is False
    # Empty config compiles to the library defaults (both checks on) without raising.
    default = _openai_moderation({}, None)
    assert default.check_input is True and default.check_output is True
    # Advanced-JSON pass-through kwargs reach the library unchanged.
    assert _openai_moderation({"exit_behavior": "replace"}, None).exit_behavior == "replace"
