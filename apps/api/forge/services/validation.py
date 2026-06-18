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
from forge.engine.registry import NODE_REGISTRY
from forge.schemas import contracts

END_TOKENS = {"END", "__end__"}


@dataclass
class ValidationResult:
    valid: bool = True
    errors: list[dict] = field(default_factory=list)
    # Warnings don't block save/publish — they flag almost-certainly-wrong wiring
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

    for i, n in enumerate(nodes):
        if isinstance(n, dict) and n.get("type") == "router":
            cfg = n.get("config", {}) or {}
            for val, tgt in (cfg.get("cases") or {}).items():
                if tgt not in node_ids:
                    res.add(f"/nodes/{i}/config/cases/{val}", f"Router case target {tgt!r} is not a node id")
            if cfg.get("default") and cfg["default"] not in node_ids:
                res.add(f"/nodes/{i}/config/default", f"Router default {cfg['default']!r} is not a node id")

    # Routing sanity (warnings): a router whose expression is a bare state key that no
    # node writes will ALWAYS take its default path — the single most common wiring bug.
    _warn_router_writers(res, nodes)

    # Trigger sanity: a webhook marked "signed" with no resolvable secret rejects EVERY
    # request; flag it at publish rather than failing silently at request time (feature/F4).
    _warn_webhook_signing(res, nodes)

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
        # expression value matches no case — e.g. when an upstream classifier fails.
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
                f"Router branches on '{expr}', but no node writes that state key — every run will "
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
                "Webhook requires a signature but no secret_ref is set — every inbound request "
                "will be REJECTED. Set the HMAC secret, or turn off require_signature.",
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
