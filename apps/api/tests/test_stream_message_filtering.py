"""Which nodes' streamed tokens reach the chat bubble.

Classifier and structured-`llm` nodes still stream tokens over the messages channel even
though their result (a routing label / structured_response) never enters `messages`. The run
stream suppresses those by node id so a client no longer has to guess from node-name patterns
(the old `classif|router|^start$` regex). Answer producers stream normally.
"""

from __future__ import annotations

from forge.services.runs import _internal_message_nodes


def test_suppresses_classifier_router_start_and_structured_llm():
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "intent", "type": "classifier"},
        {"id": "route", "type": "router"},
        {"id": "answer", "type": "agent"},
        {"id": "reply", "type": "llm"},  # unstructured llm = an answer producer
        {"id": "extract", "type": "llm", "config": {"response_format": {"mode": "structured", "schema": {}}}},
        {"id": "finish", "type": "end"},
    ]
    suppressed = _internal_message_nodes(nodes)
    assert suppressed == {"start", "intent", "route", "extract", "finish"}
    # Answer-producing nodes stream normally.
    assert "answer" not in suppressed
    assert "reply" not in suppressed


def test_unstructured_llm_is_not_suppressed():
    # response_format present but not "structured" -> still an answer producer.
    nodes = [{"id": "reply", "type": "llm", "config": {"response_format": {"mode": "text"}}}]
    assert _internal_message_nodes(nodes) == set()


def test_tolerates_malformed_nodes():
    nodes = [None, "oops", {"type": "classifier"}, {"id": "c2", "type": "classifier"}]
    # Non-dicts and a dict without an id are skipped (a null id would match tokens that have no
    # langgraph_node and wrongly drop answers); only the well-formed classifier is suppressed.
    assert _internal_message_nodes(nodes) == {"c2"}
