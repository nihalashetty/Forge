"""Flow-control nodes: start, end, router.

- `start` / `end` are passthrough markers; the compiler wires START -> entry_node
  and every `end` node -> END.
- `router` evaluates a sandboxed expression over state and routes to a case target.
  Its outgoing routing comes from `config.cases`/`config.default` (the compiler adds
  conditional edges), so labeled canvas edges out of a router are ignored at compile.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END

from forge.engine.context import CompileContext
from forge.engine.expressions import ExpressionError, eval_expression, eval_truthy
from forge.engine.registry import NodeSpec, Port, register

log = logging.getLogger("forge.flow")


def _passthrough(state: dict) -> dict:
    return {}


def start_factory(config: dict, ctx: CompileContext):
    return _passthrough


def end_factory(config: dict, ctx: CompileContext):
    return _passthrough


def make_router_path(config: dict):
    """Build a LangGraph path function: state -> target node id (or END).

    With `multi: true`, a list-valued expression (e.g. a multi-label classifier's
    output) routes to EVERY matching case target - LangGraph runs them in parallel
    within one superstep, which is how one question with several intents reaches
    several specialists at once.
    """
    expr = config["expression"]
    cases = config.get("cases", {}) or {}
    default = config.get("default")
    multi = bool(config.get("multi", False))

    def _one(val: Any) -> str | None:
        # match by string key first (JSON object keys are strings), then raw value
        target = cases.get(str(val))
        if target is None and not isinstance(val, str) and val in cases:
            target = cases[val]
        return target

    def _path(state: dict) -> Any:
        try:
            val = eval_expression(expr, dict(state or {}))
        except ExpressionError as e:
            # A failing router expression silently ending the run is a debugging nightmare;
            # log it (and fall through to the default/END) so it's traceable (audit F10).
            log.warning("router expression %r failed: %s", expr, e)
            val = None
        if multi:
            vals = list(val) if isinstance(val, (list, tuple, set)) else ([val] if val is not None else [])
            targets: list[str] = []
            for v in vals:
                t = _one(v)
                if t and t not in targets:
                    targets.append(t)
            if targets:
                return targets
            if not default:
                log.warning("router %r matched no case and has no default; ending the run", expr)
            return default if default else END
        target = _one(val)
        if target is None:
            if not default:
                log.warning("router %r value %r matched no case and has no default; ending the run", expr, val)
            target = default
        return target if target else END

    return _path


def router_factory(config: dict, ctx: CompileContext):
    # The node itself is a passthrough; routing is added as conditional edges.
    return _passthrough


def subworkflow_factory(config: dict, ctx: CompileContext):
    """Compile a referenced workflow as a nested graph node (reusable component).

    The sub-graph shares the parent's tool/agent/auth context but carries NO checkpointer
    (the top-level workflow owns durability - same rule as embedded agents). Recursion is
    broken by tracking in-progress workflow ids on the context."""
    import dataclasses

    from forge.engine.compiler import compile_workflow

    ref = config["workflow_id"]
    sub_def = (getattr(ctx, "workflows", {}) or {}).get(ref)
    if not sub_def:
        def _missing(state: dict) -> dict:
            return {}
        return _missing

    compiling = getattr(ctx, "compiling", set())
    if ref in compiling:  # cycle: refuse to recurse
        def _cycle(state: dict) -> dict:
            return {}
        return _cycle

    compiling.add(ref)
    try:
        sub_ctx = dataclasses.replace(ctx, checkpointer=None, store=None)
        return compile_workflow(sub_def, sub_ctx)
    finally:
        compiling.discard(ref)


def loop_factory(config: dict, ctx: CompileContext):
    """Iterate: increment `_loop_count` and write `_loop` = 'continue'/'done' so a router
    can loop the body back here. Stops at `max_iter` or when `condition` is falsy. The
    body edge must return to this node (it `allows_cycle`); declare `_loop_count`/`_loop`
    in state (the canvas auto-declares node-written keys)."""
    max_iter = int(config.get("max_iter", 10))
    condition = config.get("condition")

    def _node(state: dict) -> dict:
        # `_loop_count` is the running firing count; stop once it reaches max_iter. (Counting
        # contract is intentionally stable - see test_loop_node_counts_and_stops.)
        i = int(state.get("_loop_count", 0)) + 1
        cont = i < max_iter
        if cont and condition:
            try:
                cont = eval_truthy(condition, dict(state))
            except ExpressionError as e:
                # A failing loop condition silently ending the loop is hard to debug; log it.
                log.warning("loop condition %r failed: %s", condition, e)
                cont = False
        return {"_loop_count": i, "_loop": "continue" if cont else "done"}

    return _node


def make_fanout_path(config: dict):
    """LangGraph Send-based map: run `child_node` once per item in `state[over]`, with the
    item placed at `item_key`. Children aggregate via an `add`-reducer state key."""
    from langgraph.types import Send

    over = config["over"]
    child = config["child_node"]
    item_key = config["item_key"]

    def _path(state: dict) -> Any:
        items = state.get(over) or []
        if not items:
            # An empty fan-out produces no Sends, so the child (and anything gated on its
            # aggregated output) never runs. Log it so an empty `over` isn't a silent
            # dead-end the operator can't see (audit F10).
            log.warning("parallel_fanout over %r produced no items; no children dispatched", over)
        return [Send(child, {item_key: item}) for item in items]

    return _path


def router_targets(config: dict) -> list[str]:
    cases = config.get("cases", {}) or {}
    targets = list(cases.values())
    if config.get("default"):
        targets.append(config["default"])
    return sorted(set(targets))


register(
    NodeSpec(
        type="start",
        schema_id="forge/nodes/start",
        input_ports=[],
        output_ports=[Port(id="out", io_type="control", direction="out")],
        factory=start_factory,
        category="flow",
        label="Start",
        description="Entry marker",
        summarize=lambda c: [],
    )
)

register(
    NodeSpec(
        type="end",
        schema_id="forge/nodes/end",
        input_ports=[Port(id="in", io_type="control", direction="in")],
        output_ports=[],
        factory=end_factory,
        category="flow",
        label="End",
        description="Terminal node",
        summarize=lambda c: [],
    )
)

register(
    NodeSpec(
        type="loop",
        schema_id="forge/nodes/loop",
        input_ports=[Port(id="in", io_type="any", direction="in")],
        output_ports=[Port(id="out", io_type="any", direction="out")],
        factory=loop_factory,
        allows_cycle=True,
        category="flow",
        label="Loop",
        description="Iterate the body until a condition/max-iterations (writes _loop=continue/done).",
        summarize=lambda c: [f"max {c.get('max_iter', 10)}", c.get("condition", "")[:32]],
    )
)

register(
    NodeSpec(
        type="parallel_fanout",
        schema_id="forge/nodes/parallel_fanout",
        input_ports=[Port(id="in", io_type="json", direction="in")],
        output_ports=[Port(id="out", io_type="control", direction="out", many=True)],
        factory=lambda c, ctx: _passthrough,
        category="flow",
        label="Parallel Fanout",
        description="Map a list: run a child node per item in parallel (Send).",
        summarize=lambda c: [f"over {c.get('over', '-')} → {c.get('child_node', '-')}"],
    )
)

register(
    NodeSpec(
        type="join",
        schema_id="forge/nodes/join",
        input_ports=[Port(id="in", io_type="control", direction="in", many=True)],
        output_ports=[Port(id="out", io_type="json", direction="out")],
        factory=lambda c, ctx: _passthrough,
        category="flow",
        label="Join",
        description="Converge parallel branches (results aggregate via an add-reducer state key).",
        summarize=lambda c: [f"reducer · {c.get('reducer', 'concat')}"],
    )
)

register(
    NodeSpec(
        type="subworkflow",
        schema_id="forge/nodes/subworkflow",
        input_ports=[Port(id="in", io_type="any", direction="in")],
        output_ports=[Port(id="out", io_type="any", direction="out")],
        factory=subworkflow_factory,
        category="flow",
        label="Subworkflow",
        description="Run another workflow as a reusable component.",
        summarize=lambda c: [f"→ {c.get('workflow_id', '-')}"],
    )
)

register(
    NodeSpec(
        type="router",
        schema_id="forge/nodes/router",
        input_ports=[Port(id="in", io_type="any", direction="in")],
        output_ports=[Port(id="out", io_type="control", direction="out", many=True)],
        factory=router_factory,
        category="flow",
        label="Router",
        description="Conditional branch",
        summarize=lambda c: [
            f"expression · {c.get('expression', '')}" + (" · multi" if c.get("multi") else ""),
            " · ".join(list((c.get('cases') or {}).keys()) + (["default"] if c.get("default") else [])),
        ],
    )
)
