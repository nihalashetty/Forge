"""Workflow validation (Doc 4 §9 rule 1): schema validation + structural rules.

Three layers:
1. validate the whole definition against `forge/workflow` (shape).
2. validate every node `config` against its node-type schema, and every middleware
   entry `config` against its per-type schema.
3. structural rules: entry exists, edge endpoints exist, no orphans, a path to END,
   and cycles only through nodes whose `NodeSpec.allows_cycle` is true.

Returns field-pointer errors so the UI can jump to the offending control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import forge.nodes  # noqa: F401  (ensure node types are registered)
from forge.engine.registry import NODE_REGISTRY, io_compatible
from forge.schemas import contracts

END_TOKENS = {"END", "__end__"}


@dataclass
class ValidationResult:
    valid: bool = True
    errors: list[dict] = field(default_factory=list)
    # Warnings don't block save/publish - they flag almost-certainly-wrong wiring
    # (e.g. a router branching on a state key nothing writes).
    warnings: list[dict] = field(default_factory=list)

    def add(self, pointer: str, message: str, **extra) -> None:
        self.valid = False
        self.errors.append({"pointer": pointer, "message": message, **extra})

    def warn(self, pointer: str, message: str, **extra) -> None:
        self.warnings.append({"pointer": pointer, "message": message, **extra})


def _adjacency(definition: dict) -> dict[str, set[str]]:
    """Build node-id -> set(target node-ids), modeling routers/branches. END omitted."""
    nodes = {n["id"]: n for n in definition["nodes"]}
    adj: dict[str, set[str]] = {nid: set() for nid in nodes}
    routed: set[str] = set()

    for n in definition["nodes"]:
        if n["type"] == "router":
            cfg = n.get("config", {}) or {}
            for tgt in list((cfg.get("cases") or {}).values()) + ([cfg.get("default")] if cfg.get("default") else []):
                if tgt in nodes:
                    adj[n["id"]].add(tgt)
            routed.add(n["id"])
        # A parallel_fanout routes to its child_node via Send (no explicit edge), so model that
        # here too - otherwise the child (and everything after it) is wrongly reported
        # unreachable / no-path-to-END for every valid fan-out workflow (pre-existing gap).
        if n["type"] == "parallel_fanout":
            cfg = n.get("config", {}) or {}
            child = cfg.get("child_node")
            if child in nodes:
                adj[n["id"]].add(child)
            routed.add(n["id"])
        if n["type"] == "end":
            adj[n["id"]].add("__END__")

    for e in definition.get("edges", []):
        src = e["source"]
        if src in routed or src not in adj:
            continue
        if e.get("branches"):
            for tgt in e["branches"].values():
                if tgt in nodes:
                    adj[src].add(tgt)
        else:
            tgt = e["target"]
            adj[src].add("__END__" if tgt in END_TOKENS else tgt)
    return adj


def _find_bad_cycle(adj: dict[str, set[str]], node_types: dict[str, str]) -> list[str] | None:
    """Return a cycle (list of node ids) that passes through a non-cycle node, else None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(adj, WHITE)
    stack: list[str] = []

    def dfs(u: str) -> list[str] | None:
        color[u] = GRAY
        stack.append(u)
        for v in adj[u]:
            if v == "__END__" or v not in color:
                continue
            if color[v] == GRAY:  # back edge -> cycle from v..u
                cycle = stack[stack.index(v):]
                if any(not _allows_cycle(node_types.get(n)) for n in cycle):
                    return cycle
            elif color[v] == WHITE:
                found = dfs(v)
                if found:
                    return found
        stack.pop()
        color[u] = BLACK
        return None

    for n in adj:
        if color[n] == WHITE:
            res = dfs(n)
            if res:
                return res
    return None


def _allows_cycle(node_type: str | None) -> bool:
    spec = NODE_REGISTRY.get(node_type or "")
    return bool(spec and spec.allows_cycle)


def validate_workflow(definition: dict) -> ValidationResult:
    res = ValidationResult()

    # Layer 1: shape.
    for err in contracts.validate_against_id(definition, "forge/workflow"):
        res.add(err["pointer"], err["message"])

    if not isinstance(definition, dict) or "nodes" not in definition:
        return res  # too malformed to continue

    nodes = definition.get("nodes", [])
    node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    node_types = {n["id"]: n["type"] for n in nodes if isinstance(n, dict) and "id" in n and "type" in n}

    # Layer 2: per-node config + per-middleware config.
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        ntype = n.get("type")
        if ntype not in NODE_REGISTRY:
            res.add(f"/nodes/{i}/type", f"Unknown node type {ntype!r}")
            continue
        cfg = n.get("config", {}) or {}
        for err in contracts.validate(cfg, contracts.node_schema_ref(ntype)):
            res.add(f"/nodes/{i}/config{err['pointer'] if err['pointer'] != '/' else ''}", err["message"], node_id=n.get("id"))
        _validate_mw(res, cfg.get("middleware", []), f"/nodes/{i}/config/middleware")

    _validate_mw(res, definition.get("global_middleware", []), "/global_middleware")

    # Layer 3: structural rules.
    entry = definition.get("entry_node")
    if entry and entry not in node_ids:
        res.add("/entry_node", f"entry_node {entry!r} is not a node id")

    for j, e in enumerate(definition.get("edges", [])):
        if not isinstance(e, dict):
            continue
        if e.get("source") not in node_ids:
            res.add(f"/edges/{j}/source", f"Unknown source node {e.get('source')!r}")
        tgt = e.get("target")
        if tgt not in node_ids and tgt not in END_TOKENS:
            res.add(f"/edges/{j}/target", f"Unknown target node {tgt!r}")
        for val, btgt in (e.get("branches") or {}).items():
            if btgt not in node_ids:
                res.add(f"/edges/{j}/branches/{val}", f"Unknown branch target {btgt!r}")
        # A branches edge routes on a `condition` expression; without one it can only ever
        # fall through to END (see compiler._branch_path), silently dead-ending the run
        # (audit F10c). Require the condition so the branch map can actually be reached.
        if e.get("branches") and not str(e.get("condition") or "").strip():
            res.add(
                f"/edges/{j}/condition",
                "Edge defines branches but has no condition, so it can only route to END. "
                "Add a condition expression whose value selects one of the branch keys.",
            )

    for i, n in enumerate(nodes):
        if isinstance(n, dict) and n.get("type") == "router":
            cfg = n.get("config", {}) or {}
            for val, tgt in (cfg.get("cases") or {}).items():
                if tgt not in node_ids:
                    res.add(f"/nodes/{i}/config/cases/{val}", f"Router case target {tgt!r} is not a node id")
            if cfg.get("default") and cfg["default"] not in node_ids:
                res.add(f"/nodes/{i}/config/default", f"Router default {cfg['default']!r} is not a node id")

    # Routing sanity (warnings): a router whose expression is a bare state key that no
    # node writes will ALWAYS take its default path - the single most common wiring bug.
    _warn_router_writers(res, nodes)

    # Trigger sanity: a webhook marked "signed" with no resolvable secret rejects EVERY
    # request; flag it at publish rather than failing silently at request time (feature/F4).
    _warn_webhook_signing(res, nodes)

    # State-write sanity: a node whose output_key isn't a declared state field has its write
    # SILENTLY DROPPED at runtime (LangGraph applies updates only to declared channels), so a
    # downstream router/agent sees nothing - error with a fix-it message (audit F10a).
    _check_undeclared_writes(res, definition)

    # io-type sanity (warnings): flag a data-node edge whose producer/consumer port types are
    # incompatible (audit F10d). Conservative - skips control/any ports and message consumers.
    _warn_io_incompatible_edges(res, definition, node_types)

    # Agent fields exposed in the UI but not yet enforced by the compiler (audit F9): warn so
    # users aren't misled into thinking they take effect.
    _warn_unwired_agent_fields(res, nodes)

    if entry in node_ids:
        adj = _adjacency(definition)
        reachable: set[str] = set()
        stack = [entry]
        while stack:
            u = stack.pop()
            if u in reachable or u == "__END__":
                continue
            reachable.add(u)
            stack.extend(adj.get(u, set()))
        orphans = node_ids - reachable
        for orphan in sorted(o for o in orphans if o):
            res.add("/nodes", f"Node {orphan!r} is unreachable from entry node", node_id=orphan)
        reaches_end = any("__END__" in adj.get(n, set()) for n in reachable)
        if not reaches_end:
            res.add("/edges", "No path reaches END (workflow would not terminate)")
        # Dead-end warning (audit F10b): a reachable, non-`end` node with no outgoing edge just
        # halts that branch without hitting END - almost always a missing edge. (routers/fanouts
        # carry their routing in config, modeled in _adjacency, so they're covered too.)
        for node_id in sorted(n for n in reachable if n):
            if node_types.get(node_id) != "end" and not adj.get(node_id):
                res.warn(
                    "/nodes",
                    f"Node {node_id!r} is reachable but has no outgoing edge, so the run stops "
                    f"here without reaching an End node. Add an edge to the next node or to END.",
                    node_id=node_id,
                )
        bad = _find_bad_cycle(adj, node_types)
        if bad:
            res.add("/edges", f"Cycle through non-loop node(s): {' -> '.join(bad)}")

    return res


_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _state_writers(nodes: list) -> dict[str, set[str]]:
    """state key -> the set of values nodes write to it (empty set = unknown values)."""
    writers: dict[str, set[str]] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        cfg = n.get("config", {}) or {}
        t = n.get("type")
        if t == "classifier":
            writers.setdefault(cfg.get("output_key", "intent"), set()).update(cfg.get("labels") or [])
        elif t == "retrieval" and cfg.get("route_key"):
            writers.setdefault(cfg["route_key"], set()).update({"yes", "no"})
        elif t == "human_input" and cfg.get("output_key"):
            writers.setdefault(cfg["output_key"], set()).update(cfg.get("allowed_decisions") or ["approve", "reject"])
        elif t == "transform":
            writers.setdefault(cfg.get("output_key", "data"), set())
        elif t in ("tool_call", "webhook_out") and cfg.get("output_key"):
            writers.setdefault(cfg["output_key"], set())
    return writers


def _warn_router_writers(res: ValidationResult, nodes: list) -> None:
    writers = _state_writers(nodes)
    for i, n in enumerate(nodes):
        if not (isinstance(n, dict) and n.get("type") == "router"):
            continue
        cfg = n.get("config", {}) or {}
        # A router with no Default ends the run SILENTLY (no answer) whenever the
        # expression value matches no case - e.g. when an upstream classifier fails.
        if not cfg.get("default"):
            res.warn(
                f"/nodes/{i}/config/default",
                "Router has no Default path: if the expression value matches no case, the run "
                "ends silently with no answer. Add a default (e.g. a general/fallback agent).",
                node_id=n.get("id"),
            )
        expr = (cfg.get("expression") or "").strip()
        if not expr or not _IDENT.match(expr):
            continue  # complex expressions are out of scope for this heuristic
        if expr not in writers:
            res.warn(
                f"/nodes/{i}/config/expression",
                f"Router branches on '{expr}', but no node writes that state key - every run will "
                f"take the Default path. Write it first (Classifier output, Q&A/Retrieval route flag, "
                f"or Human Input decision flag).",
                node_id=n.get("id"),
            )
            continue
        known = writers[expr]
        if known:
            for case_key in (cfg.get("cases") or {}):
                if str(case_key) not in known:
                    res.warn(
                        f"/nodes/{i}/config/cases/{case_key}",
                        f"Case '{case_key}' will never match: '{expr}' only takes the values "
                        f"{sorted(known)}. Case keys must be the VALUE, not a label.",
                        node_id=n.get("id"),
                    )


def _warn_webhook_signing(res: ValidationResult, nodes: list) -> None:
    for i, n in enumerate(nodes):
        if not (isinstance(n, dict) and n.get("type") == "webhook_in"):
            continue
        cfg = n.get("config", {}) or {}
        if cfg.get("require_signature") and not cfg.get("secret_ref"):
            res.warn(
                f"/nodes/{i}/config/secret_ref",
                "Webhook requires a signature but no secret_ref is set - every inbound request "
                "will be REJECTED. Set the HMAC secret, or turn off require_signature.",
                node_id=n.get("id"),
            )


# state.py always provides `messages`; `structured_response` is the conventional channel for
# structured llm/agent output; loop nodes manage their own private `_loop`/`_loop_count`. All
# are treated as implicitly declared so the declared-state check doesn't false-flag them.
_IMPLICIT_STATE_KEYS = frozenset({"messages", "structured_response", "_loop", "_loop_count"})


def _node_output_keys(node: dict) -> list[tuple[str, str]]:
    """(config-field, state-key) pairs a node WRITES via its config, for the declared-state
    check. Nodes that only ever write messages/structured_response are omitted (those keys are
    always present); the loop node's private keys are implicit too."""
    cfg = node.get("config", {}) or {}
    t = node.get("type")
    if t == "classifier":
        return [("output_key", cfg.get("output_key", "intent"))]
    if t == "transform":
        return [("output_key", cfg.get("output_key", "data"))]
    if t == "tool_call":
        return [("output_key", cfg.get("output_key", "tool_result"))]
    if t == "webhook_out":
        return [("output_key", cfg.get("output_key", "webhook_result"))]
    if t == "retrieval" and cfg.get("route_key"):
        return [("route_key", cfg["route_key"])]
    if t == "human_input" and cfg.get("output_key"):
        return [("output_key", cfg["output_key"])]
    return []


def _check_undeclared_writes(res: ValidationResult, definition: dict) -> None:
    declared = set((definition.get("state") or {}).keys()) | _IMPLICIT_STATE_KEYS
    for i, n in enumerate(definition.get("nodes", [])):
        if not isinstance(n, dict):
            continue
        for field_name, key in _node_output_keys(n):
            if key and key not in declared:
                res.add(
                    f"/nodes/{i}/config/{field_name}",
                    f"Node {n.get('id')!r} writes state key {key!r}, which is not declared in the "
                    f"workflow State - the write is silently dropped at runtime (a downstream "
                    f"router/agent would see nothing). Add {key!r} to the State schema.",
                    node_id=n.get("id"),
                )


# Nodes that read the conversation from shared state (not the incoming edge's data), so an
# io-type mismatch on the edge INTO them is expected and shouldn't be flagged.
_MESSAGE_CONSUMERS = frozenset({"agent", "deep_agent", "llm", "classifier", "human_input", "handoff"})


def _port_io(ports: list, handle: str | None) -> str | None:
    if not ports:
        return None
    if handle:
        for p in ports:
            if p.id == handle:
                return p.io_type
    return ports[0].io_type


def _warn_io_incompatible_edges(res: ValidationResult, definition: dict, node_types: dict) -> None:
    """Conservatively flag data-pipe edges whose producer/consumer port types are incompatible
    (audit F10d). Skips control/any ports, router/fanout producers (they route via config), and
    message consumers (they read from state), so ordinary control-flow edges never false-fire."""
    for j, e in enumerate(definition.get("edges", [])):
        if not isinstance(e, dict) or e.get("branches"):
            continue
        src, tgt = e.get("source"), e.get("target")
        if tgt in END_TOKENS:
            continue
        s_type, t_type = node_types.get(src), node_types.get(tgt)
        if s_type in ("router", "parallel_fanout") or t_type in _MESSAGE_CONSUMERS:
            continue
        s_spec, t_spec = NODE_REGISTRY.get(s_type or ""), NODE_REGISTRY.get(t_type or "")
        if not s_spec or not t_spec:
            continue
        s_io = _port_io(s_spec.output_ports, e.get("source_handle"))
        t_io = _port_io(t_spec.input_ports, e.get("target_handle"))
        if not s_io or not t_io or s_io in ("any", "control") or t_io in ("any", "control"):
            continue
        if not io_compatible(s_io, t_io):
            res.warn(
                f"/edges/{j}",
                f"Edge {src!r} → {tgt!r} connects a {s_io!r} output to a {t_io!r} input, which are "
                f"incompatible port types. Double-check the wiring.",
            )


def _warn_unwired_agent_fields(res: ValidationResult, nodes: list) -> None:
    """Warn when an agent node sets fields the compiler doesn't (yet) enforce, so the config
    isn't silently ignored (audit F9). dynamic_prompt/dynamic_model/skills ARE wired now, so
    only memory, filesystem, and permissions remain."""
    for i, n in enumerate(nodes):
        if not (isinstance(n, dict) and n.get("type") in ("agent", "deep_agent")):
            continue
        cfg = n.get("config", {}) or {}
        mem = cfg.get("memory") or {}
        if mem.get("long_term") or mem.get("store_namespace") or mem.get("state_extensions"):
            res.warn(
                f"/nodes/{i}/config/memory",
                "Agent 'memory' (long-term / store namespace / state extensions) is exposed but "
                "not yet enforced by the compiler - it currently has no effect.",
                node_id=n.get("id"),
            )
        if cfg.get("filesystem"):
            res.warn(
                f"/nodes/{i}/config/filesystem",
                "Deep-agent 'filesystem' backend config is not yet wired - the default backend is "
                "used regardless of this setting.",
                node_id=n.get("id"),
            )
        if cfg.get("permissions"):
            res.warn(
                f"/nodes/{i}/config/permissions",
                "Deep-agent 'permissions' are exposed but NOT yet enforced by the compiler - do "
                "not rely on them as a security control.",
                node_id=n.get("id"),
            )


def _validate_mw(res: ValidationResult, stack: list, base_pointer: str) -> None:
    for k, entry in enumerate(stack or []):
        if not isinstance(entry, dict):
            continue
        mtype = entry.get("type")
        schema = contracts.middleware_config_schema(mtype)
        if schema is None:
            res.add(f"{base_pointer}/{k}/type", f"Unknown middleware type {mtype!r}")
            continue
        for err in contracts.validate(entry.get("config", {}) or {}, schema):
            ptr = err["pointer"] if err["pointer"] != "/" else ""
            res.add(f"{base_pointer}/{k}/config{ptr}", err["message"])
