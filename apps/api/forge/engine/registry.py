"""Node Type Registry - `node.type` -> factory + typed ports + schema id.

Doc 2 §6. New node types are added by registering a `NodeSpec`; the compiler,
the validator, and the UI palette all read from this registry without edits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# IOType enum (Doc 4 §1). A connection is valid iff source/target are compatible.
IO_TYPES = frozenset(
    {"messages", "text", "json", "tool", "embedding", "vector", "any", "control"}
)


def io_compatible(source: str, target: str) -> bool:
    """`any` matches all; `control` only connects to `control`; else exact match."""
    if source == "control" or target == "control":
        return source == "control" and target == "control"
    if source == "any" or target == "any":
        return True
    return source == target


@dataclass(frozen=True)
class Port:
    id: str
    io_type: str
    direction: str  # "in" | "out"
    label: str | None = None
    required: bool = True
    many: bool = False


# A NodeFactory takes (validated config, CompileContext) and returns a LangGraph
# node - either a plain callable `(state) -> dict` or a compiled Runnable/graph.
NodeFactory = Callable[[dict, "object"], object]
# summarize(config) -> short glanceable lines for the canvas node body.
Summarizer = Callable[[dict], list[str]]


@dataclass
class NodeSpec:
    type: str
    schema_id: str
    input_ports: list[Port]
    output_ports: list[Port]
    factory: NodeFactory
    allows_cycle: bool = False
    summarize: Summarizer | None = None
    category: str = "flow"  # flow|agents|model_tools|knowledge|human|integrations
    label: str = ""
    description: str = ""


NODE_REGISTRY: dict[str, NodeSpec] = {}


def register(spec: NodeSpec) -> NodeSpec:
    if spec.type in NODE_REGISTRY:
        raise ValueError(f"Node type already registered: {spec.type!r}")
    NODE_REGISTRY[spec.type] = spec
    return spec


def get_spec(node_type: str) -> NodeSpec:
    try:
        return NODE_REGISTRY[node_type]
    except KeyError as e:
        known = ", ".join(sorted(NODE_REGISTRY)) or "<none registered>"
        raise KeyError(f"Unknown node type {node_type!r}. Registered: {known}") from e


def all_specs() -> list[NodeSpec]:
    return list(NODE_REGISTRY.values())
