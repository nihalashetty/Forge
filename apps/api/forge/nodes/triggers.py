"""Trigger nodes — event-driven entry points (Phase 3).

A trigger node is the workflow's entry: the dispatcher (webhook route / scheduler /
inbound email / chat channel) creates a run whose `input` already carries the inbound
message, then the graph runs from the trigger node onward. At compile time a trigger is
a passthrough, exactly like `start`; its config drives the DISPATCHER (which path /
schedule / mailbox), not the graph body.

Registering them as node types means they appear in the palette, validate via schema,
and the validator's "reachable from entry / path to END" rules apply unchanged.
"""

from __future__ import annotations

from forge.engine.context import CompileContext
from forge.engine.registry import NodeSpec, Port, register

TRIGGER_TYPES = ("webhook_in", "schedule", "email_in", "chat_in", "app_event")


def _passthrough_factory(config: dict, ctx: CompileContext):
    def _node(state: dict) -> dict:
        return {}

    return _node


_out = [Port(id="out", io_type="control", direction="out")]

register(NodeSpec(
    type="webhook_in", schema_id="forge/nodes/trigger_webhook",
    input_ports=[], output_ports=_out, factory=_passthrough_factory,
    category="triggers", label="Webhook", description="Run when an external system POSTs to this workflow's hook URL.",
    summarize=lambda c: ["inbound webhook", "signed" if c.get("require_signature") else "unsigned"],
))
register(NodeSpec(
    type="schedule", schema_id="forge/nodes/trigger_schedule",
    input_ports=[], output_ports=_out, factory=_passthrough_factory,
    category="triggers", label="Schedule", description="Run on a recurring schedule (cron or every N minutes).",
    summarize=lambda c: [c.get("cron") or (f"every {c.get('every_minutes', '—')} min")],
))
register(NodeSpec(
    type="email_in", schema_id="forge/nodes/trigger_email",
    input_ports=[], output_ports=_out, factory=_passthrough_factory,
    category="triggers", label="Email", description="Run when an email arrives in the connected mailbox; optionally reply.",
    summarize=lambda c: [c.get("mailbox") or "inbound email", "reply" if c.get("reply", True) else "no reply"],
))
register(NodeSpec(
    type="chat_in", schema_id="forge/nodes/trigger_chat",
    input_ports=[], output_ports=_out, factory=_passthrough_factory,
    category="triggers", label="Chat", description="Run from a chat surface (Microsoft Teams).",
    summarize=lambda c: [f"channel · {c.get('channel', 'any')}"],
))
register(NodeSpec(
    type="app_event", schema_id="forge/nodes/trigger_app_event",
    input_ports=[], output_ports=_out, factory=_passthrough_factory,
    category="triggers", label="App Event", description="Poll an external source; run once per new item.",
    summarize=lambda c: [str(c.get("poll_url", ""))[:32], f"every {c.get('interval_minutes', 5)} min"],
))
