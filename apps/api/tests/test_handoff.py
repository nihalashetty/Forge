"""Live-agent handoff: a channel run pauses at a handoff node, opens a queue item,
and an agent reply resumes the run."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.models import HandoffRequest, Workflow
from forge.services.channels import ChannelService
from forge.services.dispatch import dispatch_message
from forge.services.handoff import HandoffService
from forge.services.runs import RunService

_WF = {
    "id": "wf_h", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "handoff",
    "nodes": [
        {"id": "handoff", "type": "handoff", "config": {"reason": "needs a human", "ack_message": "Hold on, connecting you."}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "handoff", "target": "end"}],
}


async def test_handoff_node_interrupts_then_resumes():
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_h", project_id="p_h", name="H", executable=_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        ch = await ChannelService.create(s, "t_h", "p_h", type_="teams", name="W", workflow_id=wf.id)

    # shared checkpointer so the run can be resumed by id
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_message(rs, tenant_id="t_h", project_id="p_h", workflow_id=wf.id, text="I need help")
    assert result["interrupted"] is True
    # the interrupt carries our handoff marker + reason
    flat = [it for grp in result["interrupts"] for it in grp]
    assert any(isinstance(i.get("value"), dict) and i["value"].get("handoff") for i in flat)

    # open a handoff queue item and have an agent reply
    async with SessionLocal() as s:
        h = await HandoffService.create(
            s, channel=ch, tenant_id="t_h", project_id="p_h", workflow_id=wf.id,
            run_id=result["run_id"], thread_id=result["thread_id"], customer="widget-user",
            customer_message="I need help", reason="needs a human", reply_context={},
        )
        out = await HandoffService.reply(s, rs, handoff=h, agent_id="agent1", message="Hi, this is Sam - happy to help!")
        assert out["ok"] is True
        refreshed = await s.get(HandoffRequest, h.id)
        assert refreshed.status == "answered" and refreshed.agent_id == "agent1"
