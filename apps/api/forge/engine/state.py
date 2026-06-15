"""Build a runtime state schema (TypedDict) from a workflow's declared state.

Doc 2 §6 / Doc 4 §2: state is a `TypedDict` (NOT pydantic, hard v1 constraint).
Each field maps to a python type + a reducer so parallel writes merge correctly.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages

# Doc 4 StateFieldSpec.type -> python type used for the channel.
PY_TYPES: dict[str, Any] = {
    "list[message]": list,
    "list[str]": list,
    "list[json]": list,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "json": dict,
}


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


# Doc 4 StateFieldSpec.reducer -> binary reducer. "last" => no reducer (LastValue/overwrite).
REDUCERS: dict[str, Any] = {
    "add_messages": add_messages,
    "add": operator.add,
    "merge": _merge,
    # "last" intentionally absent: a plain annotation => overwrite semantics.
}

# Sensible default so agent nodes always have a messages channel to read/write.
_DEFAULT_MESSAGES = {"type": "list[message]", "reducer": "add_messages"}


def build_state_typeddict(state_cfg: dict[str, dict], name: str = "WorkflowState") -> type:
    """Compile a state-schema dict into a `TypedDict` with reducer annotations.

    Example input (executable JSON `state`):
        {"messages": {"type": "list[message]", "reducer": "add_messages"},
         "findings": {"type": "list[str]",     "reducer": "add"},
         "intent":   {"type": "str",           "reducer": "last"}}
    """
    cfg = dict(state_cfg or {})
    cfg.setdefault("messages", _DEFAULT_MESSAGES)

    annotations: dict[str, Any] = {}
    for field, spec in cfg.items():
        py = PY_TYPES.get(spec.get("type", "json"), Any)
        reducer_name = spec.get("reducer", "last")
        reducer = REDUCERS.get(reducer_name)
        annotations[field] = Annotated[py, reducer] if reducer is not None else py

    # Functional TypedDict carries Annotated reducer metadata that LangGraph reads
    # when building channels. total=False: nodes may write a partial state update.
    return TypedDict(name, annotations, total=False)  # type: ignore[operator]
