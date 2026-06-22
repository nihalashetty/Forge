"""Error-workflow fallback: an erroring run returns the on_error message gracefully."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.models import Workflow
from forge.services.dispatch import dispatch_message
from forge.services.runs import RunService

# agent with an unknown middleware type -> compile_workflow raises (deterministic, offline).
# run_to_completion compiles inside its try block, so the on_error fallback covers it.
_ERR_WF = {
    "id": "wf_err", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "agent",
    "on_error": {"message": "Sorry - something went wrong. A teammate will follow up."},
    "nodes": [
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:hi", "middleware": [{"type": "___nonexistent___", "enabled": True}]}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "agent", "target": "end"}],
}


async def test_errored_run_returns_on_error_message():
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_err", project_id="p_err", name="Err", executable=_ERR_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_message(rs, tenant_id="t_err", project_id="p_err", workflow_id=wf.id, text="hi")
    assert result.get("error")  # the run did fail
    assert result.get("error_handled") is True
    assert result.get("answer") == "Sorry - something went wrong. A teammate will follow up."
