"""The workflow compiler (Doc 2 §6): executable JSON -> `CompiledStateGraph`.

Topologically agnostic - it trusts the validator (schemas/workflow.json + extra
rules) to have already rejected bad definitions. Routing:

- `router` nodes route via their own `config.cases`/`default` (conditional edges);
  their labeled out-edges in `edges[]` are ignored.
- edges with `branches` (value->target) become conditional edges keyed by an
  optional `condition` expression.
- `end` nodes are wired to END; plain edges are added as-is.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

import forge.nodes  # noqa: F401  (import registers all built-in node types)
from forge.engine.context import CompileContext
from forge.engine.expressions import ExpressionError, eval_expression
from forge.engine.registry import get_spec
from forge.engine.state import build_state_typeddict
from forge.nodes.flow import make_fanout_path, make_router_path, router_targets


def _branch_path(condition: str | None, mapping: dict[str, str]):
    def _path(state: dict) -> Any:
        if not condition:
            return END
        try:
            val = str(eval_expression(condition, dict(state or {})))
        except ExpressionError:
            return END
        return mapping.get(val, END)

    return _path


def compile_workflow(definition: dict, ctx: CompileContext):
    """Compile an executable workflow definition into a runnable LangGraph graph."""
    state_schema = build_state_typeddict(definition.get("state", {}))
    builder = StateGraph(state_schema)

    nodes = definition["nodes"]

    # 1) add every node from its registered factory
    for n in nodes:
        spec = get_spec(n["type"])
        builder.add_node(n["id"], spec.factory(n.get("config", {}) or {}, ctx))

    # 2) terminal markers -> END
    for n in nodes:
        if n["type"] == "end":
            builder.add_edge(n["id"], END)

    # 3) router nodes -> conditional edges from their own config
    routed: set[str] = set()
    for n in nodes:
        if n["type"] == "router":
            cfg = n.get("config", {}) or {}
            targets = router_targets(cfg) or [END]
            builder.add_conditional_edges(n["id"], make_router_path(cfg), targets)
            routed.add(n["id"])

    # 3b) parallel_fanout nodes -> Send-based map to their child_node (skip normal edges)
    for n in nodes:
        if n["type"] == "parallel_fanout":
            cfg = n.get("config", {}) or {}
            child = cfg.get("child_node")
            if child:
                builder.add_conditional_edges(n["id"], make_fanout_path(cfg), [child])
                routed.add(n["id"])

    # 4) explicit edges (skip any whose source is a self-routing router / fanout)
    for e in definition.get("edges", []):
        src = e["source"]
        if src in routed:
            continue
        if e.get("branches"):
            mapping = {str(k): v for k, v in e["branches"].items()}
            builder.add_conditional_edges(
                src, _branch_path(e.get("condition"), mapping), sorted(set(mapping.values()))
            )
        else:
            tgt = e["target"]
            builder.add_edge(src, END if tgt in ("END", "__end__") else tgt)

    # 5) entry
    builder.add_edge(START, definition["entry_node"])

    return builder.compile(checkpointer=ctx.checkpointer, store=ctx.store)
