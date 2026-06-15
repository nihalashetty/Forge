"""Eval harness (dataset run + scoring) and ephemeral retrieval context."""

from __future__ import annotations

from langchain_core.messages import RemoveMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.engine.context import CompileContext
from forge.models import Workflow
from forge.nodes.rag import _KB_TAG, retrieval_factory
from forge.services.evals import EvalService, _score_deterministic
from forge.services.runs import RunService

# --- ephemeral retrieval ---


async def test_retrieval_removes_prior_kb_message():
    ctx = CompileContext(tenant_id="t_r", project_id="p_r")
    node = retrieval_factory({"announce_empty": True, "top_k": 2}, ctx)
    prior = SystemMessage(content="old KB context", additional_kwargs={_KB_TAG: True})
    prior.id = "kb-old"
    user = {"role": "user", "content": "anything"}
    out = await node({"messages": [prior, user]})
    msgs = out.get("messages", [])
    # prior KB message is removed; a fresh tagged one is added
    assert any(isinstance(m, RemoveMessage) and m.id == "kb-old" for m in msgs)
    assert any(isinstance(m, SystemMessage) and m.additional_kwargs.get(_KB_TAG) for m in msgs)


def test_score_modes():
    assert _score_deterministic("contains", "The answer is 42 friend", "42") is True
    assert _score_deterministic("exact", "42", "42") is True
    assert _score_deterministic("exact", "the answer is 42", "42") is False
    assert _score_deterministic("regex", "order #A-1007 shipped", r"#A-\d+") is True


# --- eval run ---

_WF = {
    "id": "wf_e", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:Your order total is 42 dollars."}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}


async def test_eval_run_scores_dataset():
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_e", project_id="p_e", name="E", executable=_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        ds = await EvalService.create(s, "t_e", "p_e", name="smoke", workflow_id=wf.id, score_mode="contains",
                                      items=[{"input": "total?", "expected": "42"}, {"input": "hi", "expected": "nonexistent-string"}])
        rs = RunService(checkpointer=InMemorySaver())
        report = await EvalService.run(s, rs, ds)
    assert report["summary"]["total"] == 2
    assert report["summary"]["passed"] == 1  # first contains "42", second does not
    assert report["results"][0]["passed"] is True and report["results"][1]["passed"] is False
    assert ds.last_pass_rate == 0.5
