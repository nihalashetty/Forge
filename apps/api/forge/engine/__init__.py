"""Forge execution engine: JSON workflow definitions -> compiled LangGraph graphs.

The heart of the platform. Public surface:

- `registry`            — Node Type Registry (`NodeSpec`, `Port`, `register`).
- `compile_workflow`    — executable JSON -> `CompiledStateGraph`.
- `build_state_typeddict` — state schema dict -> runtime `TypedDict` with reducers.
- `build_middleware`    — middleware stack list -> `list[AgentMiddleware]`.
- `resolve_model`       — model ref string -> chat model (provider or fake).
- `CompileContext`      — per-compile dependencies (tenant, checkpointer, tools, ...).
"""

from forge.engine.context import CompileContext
from forge.engine.models import resolve_model
from forge.engine.registry import NODE_REGISTRY, NodeSpec, Port, io_compatible, register
from forge.engine.state import build_state_typeddict

__all__ = [
    "CompileContext",
    "resolve_model",
    "NODE_REGISTRY",
    "NodeSpec",
    "Port",
    "io_compatible",
    "register",
    "build_state_typeddict",
]
