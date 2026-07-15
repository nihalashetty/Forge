"""Data / integration nodes: transform, human_input, webhook_out, emit_event.

Convention: data nodes read from an optional `input_key` (else the whole state)
and write to `output_key` (which MUST be a declared state field, else LangGraph
rejects the update). `human_input` writes the decision into `messages`.

(`tool_call` and the RAG node land next: tool_call needs per-user context
plumbing for auth'd tools; retrieval needs the Chroma store.)
"""

from __future__ import annotations

import logging
from typing import Any

import jmespath

from forge.auth_providers.templates import render_value
from forge.engine.context import CompileContext
from forge.engine.registry import NodeSpec, Port, register

log = logging.getLogger("forge.data")


def _jq_transform(expr: str, input_key: str | None, output_key: str):
    """Build a jq-powered transform node. jq is optional; if the `jq` package isn't installed
    we raise a clear ValueError WHEN THE NODE RUNS rather than silently falling back to
    JMESPath (which speaks a different language and would quietly produce wrong data) - audit
    F7. Compile succeeds so the rest of the workflow still previews."""
    try:
        import jq as _jq
    except ImportError:
        def _unavailable(state: dict) -> dict:
            raise ValueError(
                "transform engine 'jq' requires the `jq` package, which is not installed. "
                "Install it (pip install jq) or switch this transform's engine to 'jmespath'."
            )
        return _unavailable

    try:
        program = _jq.compile(expr)  # a malformed program surfaces here, at compile time
    except Exception as e:  # noqa: BLE001 - re-raise as a clear config error
        raise ValueError(f"Invalid jq expression {expr!r}: {e}") from e

    def _node(state: dict) -> dict:
        src = state.get(input_key) if input_key else dict(state)
        try:
            result: Any = program.input(src).first()
        except Exception as e:  # noqa: BLE001 - a runtime jq failure -> None, but log it
            log.warning("transform jq %r failed: %s: %s", expr, type(e).__name__, e)
            result = None
        return {output_key: result}

    return _node


def transform_factory(cfg: dict, ctx: CompileContext):
    expr = cfg["expression"]
    engine = cfg.get("engine", "jmespath")
    input_key = cfg.get("input_key")
    output_key = cfg.get("output_key", "data")

    if engine == "jq":
        return _jq_transform(expr, input_key, output_key)

    def _node(state: dict) -> dict:
        src = state.get(input_key) if input_key else dict(state)
        try:
            result: Any = jmespath.search(expr, src)
        except jmespath.exceptions.JMESPathError as e:
            # Previously swallowed to None silently, which hid typo'd expressions; log it so a
            # broken transform is traceable in the run log (audit F7).
            log.warning("transform jmespath %r failed: %s: %s", expr, type(e).__name__, e)
            result = None
        return {output_key: result}

    return _node


def human_input_factory(cfg: dict, ctx: CompileContext):
    from langchain_core.messages import HumanMessage
    from langgraph.types import interrupt

    from forge.services.runs import HITL_APPROVAL_TIMEOUT_SECONDS

    prompt = cfg["prompt"]
    decisions = cfg.get("allowed_decisions", ["approve", "reject"])
    schema = cfg.get("schema")
    # When set, also write the decision string to this state key so a downstream router
    # can branch on it (approve → continue, reject → end). The key must be declared in
    # workflow state (the canvas auto-declares node-written keys).
    output_key = cfg.get("output_key")
    # Deadline surfaced on the interrupt so operators/UI see how long the approval waits before
    # the reaper expires it (audit C). Per-node override, else the global HITL timeout (0 = none).
    timeout_seconds = cfg.get("timeout_seconds") or HITL_APPROVAL_TIMEOUT_SECONDS or None
    timeout_default = cfg.get("timeout_default")
    if timeout_default not in decisions:
        timeout_default = None

    def _node(state: dict) -> dict:
        # Pauses the run; resumed via Command(resume=value). Node re-runs from the
        # top on resume, so the side effect (writing the decision) is placed after.
        decision = interrupt({
            "prompt": prompt, "allowed_decisions": decisions, "schema": schema,
            "timeout_seconds": timeout_seconds, "timeout_default": timeout_default,
        })
        out: dict[str, Any] = {"messages": [HumanMessage(content=f"[human decision] {decision}")]}
        if output_key:
            # Coerce a free-text resume value to one of allowed_decisions for the ROUTING key so a
            # Router keyed on approve/reject matches even on a direct API resume (audit C). The
            # transcript message above keeps the human's raw wording; only the routed value is
            # normalized. Structured (dict) input is left as-is.
            routed: Any = decision
            if isinstance(decision, str) and decisions:
                from forge.services.handoff import coerce_to_allowed_decision

                routed = coerce_to_allowed_decision(decision, list(decisions))
            out[output_key] = str(routed)
        return out

    return _node


def handoff_factory(cfg: dict, ctx: CompileContext):
    """Live-agent handoff: pause the run (interrupt) and hand the conversation to a
    human. The channel creates a HandoffRequest; when a human replies via the agent
    inbox, the run resumes with their text, which becomes the assistant's reply."""
    from langchain_core.messages import AIMessage
    from langgraph.types import interrupt

    reason = cfg.get("reason", "Escalated to a human agent.")

    def _node(state: dict) -> dict:
        reply = interrupt({"handoff": True, "reason": reason, "ack_message": cfg.get("ack_message")})
        return {"messages": [AIMessage(content=str(reply))]}

    return _node


def webhook_out_factory(cfg: dict, ctx: CompileContext):
    method = cfg["method"]
    url_t = cfg["url"]
    provider_id = cfg.get("auth_provider_id")
    output_key = cfg.get("output_key", "webhook_result")
    body_t = cfg.get("body")
    headers_t = cfg.get("headers", {})

    async def _node(state: dict) -> dict:
        from forge.util.http import shared_async_client
        from forge.util.ssrf import validate_url

        vars = {"state": dict(state)}
        url = render_value(url_t, vars)
        body = render_value(body_t, vars) if body_t else None
        headers = render_value(dict(headers_t), vars)
        params: dict[str, str] = {}
        cookies: dict[str, str] = {}
        if provider_id and ctx.auth_resolver:
            auth = await ctx.auth_resolver.resolve(
                tenant_id=ctx.tenant_id, project_id=ctx.project_id, provider_id=provider_id, context={}
            )
            headers.update(auth.headers)
            params.update(auth.params)
            cookies.update(auth.cookies)
        await validate_url(url, getattr(ctx, "egress_policy", None))
        c = shared_async_client()
        r = await c.request(method, url, headers=headers, params=params or None, json=body, cookies=cookies or None, timeout=30)
        try:
            out: Any = r.json()
        except Exception:  # noqa: BLE001
            out = r.text
        return {output_key: out}

    return _node


def tool_call_factory(cfg: dict, ctx: CompileContext):
    tool_id = cfg["tool_id"]
    input_mapping = cfg.get("input_mapping", {}) or {}
    output_key = cfg.get("output_key", "tool_result")

    async def _node(state: dict, config=None) -> dict:
        # Invoke the SAME materialized tool an agent would use, passing the run config so
        # the call is traced (the tracer is a callback on config) and so REST/GraphQL/
        # code/sql/mcp all go through one path with one error contract.
        tool = ctx.tool_registry.get(tool_id)
        if tool is None:
            return {output_key: {"error": f"tool {tool_id} not available"}}
        args: dict[str, Any] = {}
        for k, expr in input_mapping.items():
            try:
                args[k] = jmespath.search(expr, dict(state)) if isinstance(expr, str) else expr
            except jmespath.exceptions.JMESPathError:
                args[k] = expr
        try:
            out = await tool.ainvoke(args, config)
        except Exception as e:  # noqa: BLE001 - surface tool failure as a structured result
            out = {"error": str(e)}
        return {output_key: out}

    return _node


def emit_event_factory(cfg: dict, ctx: CompileContext):
    channel = cfg["channel"]
    payload_t = cfg.get("payload", {})

    def _node(state: dict) -> dict:
        try:
            from langgraph.config import get_stream_writer

            get_stream_writer()({"channel": channel, "payload": render_value(payload_t, {"state": dict(state)})})
        except Exception:  # noqa: BLE001 - no active stream writer (e.g. ainvoke)
            pass
        return {}

    return _node


_io_any = ([Port(id="in", io_type="any", direction="in")], [Port(id="out", io_type="any", direction="out")])

register(NodeSpec(
    type="transform", schema_id="forge/nodes/transform",
    input_ports=[Port(id="in", io_type="json", direction="in")],
    output_ports=[Port(id="out", io_type="json", direction="out")],
    factory=transform_factory, category="model_tools", label="Transform", description="JMESPath data map",
    summarize=lambda c: [f"{c.get('engine', 'jmespath')} · → {c.get('output_key', 'data')}"],
))
register(NodeSpec(
    type="human_input", schema_id="forge/nodes/human_input",
    input_ports=_io_any[0], output_ports=_io_any[1],
    factory=human_input_factory, category="human", label="Human Input", description="HITL pause via interrupt",
    summarize=lambda c: [c.get("prompt", "")[:40], " · ".join(c.get("allowed_decisions", ["approve", "reject"]))],
))
register(NodeSpec(
    type="tool_call", schema_id="forge/nodes/tool_call",
    input_ports=[Port(id="in", io_type="json", direction="in")],
    output_ports=[Port(id="out", io_type="json", direction="out")],
    factory=tool_call_factory, category="model_tools", label="Tool Call", description="Run a specific tool",
    summarize=lambda c: [str(c.get("tool_id", "-")), f"→ {c.get('output_key', 'tool_result')}"],
))
register(NodeSpec(
    type="webhook_out", schema_id="forge/nodes/webhook_out",
    input_ports=[Port(id="in", io_type="json", direction="in")],
    output_ports=[Port(id="out", io_type="json", direction="out")],
    factory=webhook_out_factory, category="integrations", label="Webhook", description="Call external URL",
    summarize=lambda c: [f"{c.get('method', 'POST')} {str(c.get('url', ''))[:32]}"],
))
register(NodeSpec(
    type="handoff", schema_id="forge/nodes/handoff",
    input_ports=_io_any[0], output_ports=_io_any[1],
    factory=handoff_factory, category="human", label="Human Handoff",
    description="Escalate the conversation to a human agent (pauses until they reply).",
    summarize=lambda c: [c.get("reason", "human handoff")[:40]],
))
register(NodeSpec(
    type="emit_event", schema_id="forge/nodes/emit_event",
    input_ports=_io_any[0], output_ports=_io_any[1],
    factory=emit_event_factory, category="integrations", label="Emit Event", description="Push custom SSE frame",
    summarize=lambda c: [f"channel · {c.get('channel', '')}"],
))
