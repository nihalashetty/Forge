"""Regression tests for the 2026-06-11 fix round (docs/FIXES-2026-06-11.md):
multi-label classifier + parallel router, knowledge_search builtin, KB folders,
run-thread reuse, retry_on mapping, guardrail replacement, validation warnings,
and the embedder cache.
"""

from __future__ import annotations

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.engine.compiler import compile_workflow
from forge.engine.context import CompileContext
from forge.engine.middleware_compiler import _retry_exceptions
from forge.services.validation import validate_workflow


def _ctx() -> CompileContext:
    return CompileContext(tenant_id="t1", project_id="p1", checkpointer=InMemorySaver())


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


# ---------- multi-label classifier + parallel (multi) router ----------

def _multi_wf() -> dict:
    return {
        "id": "wf_multi",
        "version": 1,
        "state": {
            "messages": {"type": "list[message]", "reducer": "add_messages"},
            "intents": {"type": "list[str]", "reducer": "last"},
        },
        "entry_node": "start",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "classify",
                "type": "classifier",
                # fake model can't do structured output -> keyword fallback collects
                # EVERY matching label (multi_label).
                "config": {"labels": ["weather", "billing"], "output_key": "intents",
                           "multi_label": True, "model": "fake:n/a"},
            },
            {
                "id": "route",
                "type": "router",
                "config": {"expression": "intents", "multi": True,
                           "cases": {"weather": "weather_agent", "billing": "billing_agent"},
                           "default": "general_agent"},
            },
            {"id": "weather_agent", "type": "agent",
             "config": {"flavor": "agent", "model": "fake:WEATHER-ANSWER"}},
            {"id": "billing_agent", "type": "agent",
             "config": {"flavor": "agent", "model": "fake:BILLING-ANSWER"}},
            {"id": "general_agent", "type": "agent",
             "config": {"flavor": "agent", "model": "fake:GENERAL-ANSWER"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "classify"},
            {"source": "classify", "target": "route"},
            {"source": "weather_agent", "target": "end"},
            {"source": "billing_agent", "target": "end"},
            {"source": "general_agent", "target": "end"},
        ],
    }


async def test_multi_label_classifier_fallback_writes_list():
    graph = compile_workflow(_multi_wf(), _ctx())
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="What's the weather like, and a question about my billing?")]},
        _cfg("multi-1"),
    )
    assert sorted(out["intents"]) == ["billing", "weather"]


async def test_multi_router_fans_out_to_all_matching_cases():
    graph = compile_workflow(_multi_wf(), _ctx())
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="weather and billing please")]},
        _cfg("multi-2"),
    )
    texts = [getattr(m, "content", "") for m in out["messages"]]
    assert any("WEATHER-ANSWER" in t for t in texts)
    assert any("BILLING-ANSWER" in t for t in texts)
    assert not any("GENERAL-ANSWER" in t for t in texts)


async def test_multi_router_falls_back_to_default_when_no_match():
    graph = compile_workflow(_multi_wf(), _ctx())
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="hello there, completely unrelated")]},
        _cfg("multi-3"),
    )
    texts = [getattr(m, "content", "") for m in out["messages"]]
    assert any("GENERAL-ANSWER" in t for t in texts)


# ---------- validation warnings ----------

def test_router_without_default_warns():
    wf = _multi_wf()
    for n in wf["nodes"]:
        if n["id"] == "route":
            n["config"].pop("default")
    wf["nodes"] = [n for n in wf["nodes"] if n["id"] != "general_agent"]
    wf["edges"] = [e for e in wf["edges"] if e["source"] != "general_agent"]
    res = validate_workflow(wf)
    assert res.valid
    assert any("no Default path" in w["message"] for w in res.warnings)


# ---------- tool_retry retry_on mapping ----------

def test_retry_exceptions_maps_names_to_types():
    excs = _retry_exceptions(["timeout", "http_error", "value_error", "bogus_name"])
    assert TimeoutError in excs
    assert httpx.HTTPError in excs
    assert ValueError in excs
    assert len(excs) == 3  # unknown names are skipped


# ---------- guardrail_regex block actually replaces ----------

async def test_guardrail_block_replaces_reply():
    wf = {
        "id": "wf_guard", "version": 1,
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
    graph = compile_workflow(wf, _ctx())
    out = await graph.ainvoke({"messages": [HumanMessage(content="hi")]}, _cfg("guard-1"))
    texts = [getattr(m, "content", "") for m in out["messages"]]
    assert any("[blocked by content guardrail]" in t for t in texts)
    assert not any("forbidden secret" in t for t in texts)


# ---------- knowledge_search builtin ----------

async def test_knowledge_search_builtin_reports_empty_kb():
    from forge.tools.materialize import materialize_tool

    tool = materialize_tool({"kind": "builtin", "builtin": "knowledge_search", "name": "kb_search"}, _ctx())
    out = await tool.ainvoke({"query": "anything at all"})
    assert "No relevant knowledge" in out


# ---------- KB folders ----------

async def test_source_folders_scope_search_and_listing():
    from forge.services.knowledge import KnowledgeService

    async with SessionLocal() as s:
        a = await KnowledgeService.create_source(
            s, "t_fold", "p_fold", kind="text", name="manual",
            text="The frobnicator manual explains frobnication in detail.", folder="Manuals")
        await KnowledgeService.ingest(s, a)
        b = await KnowledgeService.create_source(
            s, "t_fold", "p_fold", kind="text", name="policy",
            text="The vacation policy covers holidays and leave days.", folder="Policies")
        await KnowledgeService.ingest(s, b)

        folders = await KnowledgeService.list_folders(s, "t_fold", "p_fold")
        assert folders == ["Manuals", "Policies"]

        hits = await KnowledgeService.search(s, "t_fold", "p_fold", "frobnication manual", top_k=4, folders=["Manuals"])
        assert hits and all(h.metadata.get("source_id") == a.id for h in hits)

        none = await KnowledgeService.search(s, "t_fold", "p_fold", "frobnication", top_k=4, folders=["DoesNotExist"])
        assert none == []


# ---------- run thread reuse ----------

async def test_create_run_reuses_thread():
    from forge.services.runs import RunService
    from forge.services.workflows import WorkflowService

    wf_def = {
        "id": "wf", "version": 1,
        "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
        "entry_node": "start",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:ok"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "agent"}, {"source": "agent", "target": "end"}],
    }
    svc = RunService(checkpointer=InMemorySaver())
    async with SessionLocal() as s:
        wf = await WorkflowService.create(s, "t_thr", "p_thr", name="thread-reuse")
        await WorkflowService.save_canvas(s, wf, {}, wf_def)
        run1 = await svc.create_run(s, tenant_id="t_thr", project_id="p_thr", workflow_id=wf.id,
                                    input={"messages": [{"role": "user", "content": "one"}]})
        run2 = await svc.create_run(s, tenant_id="t_thr", project_id="p_thr", workflow_id=wf.id,
                                    input={"messages": [{"role": "user", "content": "two"}]},
                                    thread_id=run1.thread_id)
        run3 = await svc.create_run(s, tenant_id="t_thr", project_id="p_thr", workflow_id=wf.id,
                                    input={"messages": [{"role": "user", "content": "three"}]})
    assert run2.thread_id == run1.thread_id
    assert run3.thread_id != run1.thread_id


# ---------- embedder cache ----------

def test_embedder_cache_returns_same_instance_and_right_dims():
    from forge.knowledge.embeddings import resolve_embedder

    a = resolve_embedder("openai:text-embedding-3-small", "sk-test-cache")
    b = resolve_embedder("openai:text-embedding-3-small", "sk-test-cache")
    other_key = resolve_embedder("openai:text-embedding-3-small", "sk-different")
    large = resolve_embedder("openai:text-embedding-3-large", "sk-test-cache")
    assert a is b
    assert other_key is not a
    assert a.dim == 1536
    assert large.dim == 3072
    assert large.name == "text-embedding-3-large"


# ---------- qa kinds multi-filter + report rows ----------

async def test_lookup_kinds_list_filters_multiple_categories():
    from forge.services.knowledge import KnowledgeService

    async with SessionLocal() as s:
        await KnowledgeService.create_qa(s, "t_mk", "p_mk", question="alpha question", answer="a", kind="billing")
        await KnowledgeService.create_qa(s, "t_mk", "p_mk", question="beta question", answer="b", kind="shipping")
        await KnowledgeService.create_qa(s, "t_mk", "p_mk", question="gamma question", answer="c", kind="faq")
        hit = await KnowledgeService.lookup(s, "t_mk", "p_mk", "alpha question", threshold=0.8, kinds=["billing", "shipping"])
        assert hit and hit["kind"] == "billing"
        miss = await KnowledgeService.lookup(s, "t_mk", "p_mk", "alpha question", threshold=0.8, kinds=["faq"])
        assert miss is None
        # empty kinds list = all kinds (no filter)
        any_hit = await KnowledgeService.lookup(s, "t_mk", "p_mk", "gamma question", threshold=0.8, kinds=[])
        assert any_hit and any_hit["kind"] == "faq"


def test_report_rows_group_workflows_and_assistant():
    from types import SimpleNamespace

    from forge.routers.stats import _report_rows

    mk = lambda **kw: SimpleNamespace(total_tokens=10, total_cost_usd=0.01, latency_ms=100, **kw)  # noqa: E731
    traces = [
        mk(name="run", workflow_id="wf1"),
        mk(name="run", workflow_id="wf1"),
        mk(name="assistant", workflow_id=None),
    ]
    rows = _report_rows(traces, {"wf1": "Support"})
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["workflow"]["label"] == "Support" and by_kind["workflow"]["runs"] == 2
    assert by_kind["assistant"]["label"] == "Forge Assistant" and by_kind["assistant"]["runs"] == 1


# ---------- qa custom kinds ----------

async def test_qa_custom_kind_roundtrip_and_lookup_filter():
    from forge.services.knowledge import KnowledgeService

    async with SessionLocal() as s:
        await KnowledgeService.create_qa(s, "t_kind", "p_kind", question="How do I reset the frobnicator?",
                                         answer="Hold the red button for 5 seconds.", kind="troubleshooting")
        await KnowledgeService.create_qa(s, "t_kind", "p_kind", question="What are your business hours?",
                                         answer="9 to 5 on weekdays.", kind="faq")
        hit = await KnowledgeService.lookup(s, "t_kind", "p_kind", "How do I reset the frobnicator?",
                                            threshold=0.8, kind="troubleshooting")
        assert hit and hit["kind"] == "troubleshooting"
        miss = await KnowledgeService.lookup(s, "t_kind", "p_kind", "How do I reset the frobnicator?",
                                             threshold=0.8, kind="faq")
        assert miss is None
