"""Validate the workflow validator: schema refs, per-node config, structural rules."""

from __future__ import annotations

import copy

from forge.services.validation import validate_workflow

GOOD = {
    "id": "wf_ok",
    "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"},
              "intent": {"type": "str", "reducer": "last"}},
    "entry_node": "start",
    "nodes": [
        {"id": "start", "type": "start", "config": {}},
        {"id": "route", "type": "router", "config": {
            "expression": "intent", "cases": {"billing": "billing_agent"}, "default": "billing_agent"}},
        {"id": "billing_agent", "type": "agent", "config": {
            "flavor": "agent", "model": "anthropic:claude-sonnet-4-6",
            "middleware": [{"type": "summarization", "config": {"trigger": ["tokens", 4000]}}]}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [
        {"source": "start", "target": "route"},
        {"source": "billing_agent", "target": "end"},
    ],
}


def test_good_workflow_passes():
    res = validate_workflow(GOOD)
    assert res.valid, res.errors


def test_unknown_node_type_flagged():
    wf = copy.deepcopy(GOOD)
    wf["nodes"][0]["type"] = "frobnicate"
    res = validate_workflow(wf)
    assert not res.valid
    assert any("frobnicate" in e["message"] for e in res.errors)


def test_bad_node_config_flagged_with_pointer():
    wf = copy.deepcopy(GOOD)
    # agent requires `model`; remove it
    del wf["nodes"][2]["config"]["model"]
    res = validate_workflow(wf)
    assert not res.valid
    assert any(e["pointer"].startswith("/nodes/2/config") for e in res.errors), res.errors


def test_unknown_middleware_type_flagged():
    wf = copy.deepcopy(GOOD)
    wf["nodes"][2]["config"]["middleware"] = [{"type": "does_not_exist", "config": {}}]
    res = validate_workflow(wf)
    assert not res.valid
    assert any("does_not_exist" in e["message"] for e in res.errors)


def test_edge_to_unknown_node_flagged():
    wf = copy.deepcopy(GOOD)
    wf["edges"].append({"source": "billing_agent", "target": "ghost"})
    res = validate_workflow(wf)
    assert not res.valid
    assert any("ghost" in e["message"] for e in res.errors)


def test_orphan_node_flagged():
    wf = copy.deepcopy(GOOD)
    wf["nodes"].append({"id": "lonely", "type": "llm", "config": {"model": "fake:x", "prompt": "hi"}})
    res = validate_workflow(wf)
    assert not res.valid
    assert any("lonely" in e["message"] and "unreachable" in e["message"] for e in res.errors)


def test_no_path_to_end_flagged():
    wf = copy.deepcopy(GOOD)
    wf["edges"] = [e for e in wf["edges"] if e["target"] != "end"]  # cut the only END path
    res = validate_workflow(wf)
    assert not res.valid
    assert any("END" in e["message"] for e in res.errors)
