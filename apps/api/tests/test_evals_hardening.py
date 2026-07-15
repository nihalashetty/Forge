"""Hardening for the eval harness, tracer, quota, and build assistant (findings F1-F7).

House style: bare `async def` tests, offline `fake:` models, InMemorySaver, direct
service calls. No provider keys / network (embedding + judge paths are exercised via
their "unavailable" branches so the suite stays offline and fast).
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from forge.db.base import SessionLocal
from forge.engine.models import make_fake_model
from forge.models import Component, Dataset, McpClient, Run, Tenant, Tool, Workflow
from forge.services.evals import (
    EvalService,
    _is_real_judge,
    _score_json,
    _score_numeric,
)
from forge.services.quota import QuotaExceeded, check_run_quota, usage_today
from forge.services.runs import RunService

# A workflow whose (offline) agent always answers with "42 dollars" - lets us assert
# deterministic pass/fail without any provider key.
_WF = {
    "id": "wf_h", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:Your order total is 42 dollars."}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}


# --- F1 + F2: bounded-concurrency run, persisted history, regression gate ---


async def test_eval_run_persists_history_and_regression_gate():
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_h", project_id="p_h", name="H", executable=_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        ds = await EvalService.create(
            s, "t_h", "p_h", name="h", workflow_id=wf.id, score_mode="contains",
            items=[{"input": "a", "expected": "42"}, {"input": "b", "expected": "999"}, {"input": "c", "expected": "42"}],
        )
        ds.last_pass_rate = 1.0  # seed a prior high rate so the gate detects the drop
        await s.commit()
        rs = RunService(checkpointer=InMemorySaver())
        report = await EvalService.run(s, rs, ds, regression_gate=True)
        dsid = ds.id

    # Three items ran (concurrently); two expect "42" (present) and one expects "999".
    assert report["summary"]["total"] == 3
    assert report["summary"]["passed"] == 2
    assert report["summary"]["eval_run_id"]
    assert report["summary"]["regression"]["regressed"] is True  # 0.667 < 1.0

    async with SessionLocal() as s:
        runs = await EvalService.history(s, "t_h", dsid)
        assert len(runs) == 1
        assert runs[0].total == 3 and runs[0].passed == 2 and runs[0].prev_pass_rate == 1.0
        results = await EvalService.results(s, "t_h", runs[0].id)
        assert sorted(r.item_index for r in results) == [0, 1, 2]
        assert any(r.answer and "42" in r.answer for r in results)


# --- F3: richer scorers + per-item assertion lists (AND/OR) ---


def test_numeric_and_json_scorers():
    ok, err = _score_numeric("about 42.5 units", "42", tolerance=1.0)
    assert ok is True and err is not None
    assert _score_numeric("100", "42", tolerance=1.0)[0] is False
    assert _score_numeric("100", "90", rel_tolerance=0.2)[0] is True  # 10 <= 90*0.2
    assert _score_json('{"a": 1, "b": 2}', {"a": 1}) is True          # subset match
    assert _score_json('{"a": 1}', {"a": 1, "b": 2}) is False
    assert _score_json('{"a": 1}', {"a": 1}, mode="exact") is True
    assert _score_json("not json", {"a": 1}) is False


async def test_assertion_list_and_or_combine():
    ds = Dataset(tenant_id="t", project_id="p", name="x", score_mode="contains", items=[])
    item = {"input": "q", "expected": "42",
            "assertions": [{"type": "contains", "expected": "42"}, {"type": "contains", "expected": "zzz"}]}
    # AND (default): one check fails -> item fails.
    res_all = await EvalService._score_item(ds, {**item, "assert": "all"}, "the answer is 42", judge_model=None, embedder=None)
    assert res_all["passed"] is False and len(res_all["checks"]) == 2
    # OR: one check passes -> item passes.
    res_any = await EvalService._score_item(ds, {**item, "assert": "any"}, "the answer is 42", judge_model=None, embedder=None)
    assert res_any["passed"] is True


# --- F4: LLM judge robustness (unavailable != silent contains pass) ---


async def test_judge_unavailable_is_not_a_pass():
    assert _is_real_judge(None) is False
    assert _is_real_judge(make_fake_model("anything")) is False  # offline fake model is NOT a real judge

    passed, _reason, status = await EvalService._judge(None, "in", "expected", "answer")
    assert passed is False and status == "unavailable"

    # The answer literally CONTAINS the expected string; the old code fell back to `contains`
    # here and reported a (misleading) pass. Now a judge item with no model is "unavailable".
    ds = Dataset(tenant_id="t", project_id="p", name="x", score_mode="judge", items=[])
    res = await EvalService._score_item(ds, {"input": "i", "expected": "exp"}, "exp appears here",
                                        judge_model=None, embedder=None)
    assert res["passed"] is False and res["status"] == "unavailable"


async def test_embedding_assertion_unavailable_without_embedder():
    ds = Dataset(tenant_id="t", project_id="p", name="x", score_mode="contains", items=[])
    item = {"input": "i", "expected": "e", "assertions": [{"type": "embedding", "expected": "e", "threshold": 0.9}]}
    res = await EvalService._score_item(ds, item, "some answer", judge_model=None, embedder=None)
    assert res["passed"] is False and res["status"] == "unavailable"


# --- F5: tracer completeness (chain filtering, retriever + embedding spans) ---


def test_tracer_filters_internal_chains_and_reparents():
    from forge.tracing.tracer import ForgeTracer, _is_internal_chain

    assert _is_internal_chain("RunnableSequence") and _is_internal_chain("chain") and _is_internal_chain("__start__")
    assert not _is_internal_chain("agent_1") and not _is_internal_chain("support_agent")

    tr = ForgeTracer()
    tr.on_chain_start({"name": "RunnableSequence"}, {}, run_id="r1", parent_run_id=None)  # skipped
    tr.on_chain_start({"name": "agent_1"}, {}, run_id="r2", parent_run_id="r1")            # kept, re-parented
    tr.on_chat_model_start({}, [], run_id="r3", parent_run_id="r1")                        # kept, re-parented

    names = [s.name for s in tr.ordered()]
    assert "RunnableSequence" not in names
    assert tr.spans["r2"].kind == "chain" and tr.spans["r2"].parent_id is None  # skipped r1 had no real parent
    assert tr.spans["r3"].kind == "llm" and tr.spans["r3"].parent_id is None


def test_tracer_retriever_and_embedding_spans():
    from forge.tracing.tracer import ForgeTracer
    from forge.tracing.tracer import embedding_span as active_embedding_span

    tr = ForgeTracer()
    tr.on_retriever_start({"name": "kb"}, "my query", run_id="rr", parent_run_id=None)
    tr.on_retriever_end([{"page_content": "doc one"}, {"page_content": "doc two"}], run_id="rr")
    rspan = tr.spans["rr"]
    assert rspan.kind == "retriever" and rspan.attributes.get("docs") == 2

    # Embedding span is priced via the embedding rate in pricing.price and rolled into totals.
    with active_embedding_span("openai:text-embedding-3-small", n_texts=3, input_tokens=1000):
        pass
    emb = [s for s in tr.ordered() if s.kind == "embedding"]
    assert emb and emb[0].cost_usd > 0 and emb[0].attributes.get("n_texts") == 3


# --- F6: quota - projected-cost ceiling, per-project scoping ---


async def test_quota_projected_cost_reserves_for_inflight_runs():
    async with SessionLocal() as s:
        t = Tenant(name="PC", settings={"max_cost_per_day_usd": 1.0, "projected_run_cost_usd": 5.0})
        s.add(t)
        await s.flush()
        # An in-flight run is still $0 booked; the reservation (1 * $5) alone must trip the cap.
        s.add(Run(tenant_id=t.id, project_id="p", workflow_id="w", thread_id="th", status="running", total_cost_usd=0.0))
        await s.commit()
        tid = t.id
    async with SessionLocal() as s:
        with pytest.raises(QuotaExceeded):
            await check_run_quota(s, tid)
        u = await usage_today(s, tid)
        assert u["inflight"] == 1 and u["reserved_cost_usd"] == 5.0


async def test_quota_no_reservation_when_projected_unset():
    async with SessionLocal() as s:
        t = Tenant(name="PC2", settings={"max_cost_per_day_usd": 1.0})  # no projected reservation
        s.add(t)
        await s.flush()
        s.add(Run(tenant_id=t.id, project_id="p", workflow_id="w", thread_id="th", status="running", total_cost_usd=0.0))
        await s.commit()
        tid = t.id
    async with SessionLocal() as s:
        await check_run_quota(s, tid)  # booked cost 0 < 1 and nothing reserved -> must not raise


async def test_quota_per_project_scoping():
    async with SessionLocal() as s:
        t = Tenant(name="PP", settings={"project_limits": {"proj_a": {"max_runs_per_day": 1}}})
        s.add(t)
        await s.flush()
        s.add(Run(tenant_id=t.id, project_id="proj_a", workflow_id="w", thread_id="th", status="done"))
        await s.commit()
        tid = t.id
    async with SessionLocal() as s:
        with pytest.raises(QuotaExceeded):
            await check_run_quota(s, tid, project_id="proj_a")  # per-project cap hit
        # A project with no per-project limits (and no tenant-wide caps) is unlimited.
        await check_run_quota(s, tid, project_id="proj_b")


# --- F7: assistant build coverage (new tool kinds + component; offline judge unverified) ---


async def test_assistant_builds_all_tool_kinds_and_component():
    from forge.services.assistant import build_assistant_tools

    tools = {t.name: t for t in build_assistant_tools("t_ab", "p_ab", [])}
    for name in ("create_graphql_tool", "create_sql_tool", "create_code_tool", "create_mcp_tool", "create_component"):
        assert name in tools

    await tools["create_graphql_tool"].ainvoke({"name": "gql", "endpoint": "https://x/graphql", "query": "{ me }", "variables": "id"})
    await tools["create_sql_tool"].ainvoke({"name": "sqltool", "query": "select 1", "arg_names": "limit"})
    await tools["create_code_tool"].ainvoke({"name": "codetool", "source": "result = 1"})
    await tools["create_mcp_tool"].ainvoke({"name": "mcpsrv", "url": "https://x/mcp"})
    await tools["create_component"].ainvoke({"name": "card", "html": "<div>{{title}}</div>", "props_schema_json": '{"type": "object"}'})

    async with SessionLocal() as s:
        kinds = {t.kind for t in (await s.execute(select(Tool).where(Tool.project_id == "p_ab"))).scalars()}
        assert {"graphql", "sql", "code"} <= kinds
        assert (await s.execute(select(McpClient).where(McpClient.project_id == "p_ab"))).scalars().first() is not None
        assert (await s.execute(select(Component).where(Component.project_id == "p_ab"))).scalars().first() is not None


async def test_rest_tool_supports_body_query_header_params():
    from forge.services.assistant import build_assistant_tools

    tools = {t.name: t for t in build_assistant_tools("t_rt", "p_rt", [])}
    await tools["create_rest_tool"].ainvoke({
        "name": "post_it", "url_template": "https://x/{id}", "method": "POST",
        "query_params": "q1", "header_params": "X-H", "body_params": "b1,b2",
    })
    async with SessionLocal() as s:
        tool = (await s.execute(select(Tool).where(Tool.project_id == "p_rt"))).scalars().first()
    fields = tool.config["request"]["fields"]
    assert sorted({f["in"] for f in fields}) == ["body", "header", "path", "query"]
    assert tool.config["request"]["method"] == "POST"


async def test_evaluate_build_offline_is_unverified():
    from forge.services.assistant import build_assistant_tools

    tools = {t.name: t for t in build_assistant_tools("t_eb", "p_eb", [])}
    out = json.loads(await tools["evaluate_build"].ainvoke(
        {"user_request": "x", "what_was_built": "y", "test_results": "z"}
    ))
    # No provider key -> offline model -> NOT a bogus 'pass'.
    assert out["verdict"] == "unverified"
