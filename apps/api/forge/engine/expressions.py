"""Safe expression evaluation over run state/context (Doc 4 `Expression`).

Forge uses TWO expression languages, by role — keep them straight:
- **This one (RestrictedPython)** is for BOOLEAN/VALUE DECISIONS over state: `router`
  `expression`/`cases`, `loop` conditions, `dynamic_model_by_state`, `tenant_budget`.
  State keys are bare names, so `intent == 'billing'` and `len(messages) > 10` work.
- **JMESPath** is for DATA EXTRACTION/RESHAPING: `transform` node, tool response
  `projection_jmespath`, and `tool_call` `input_mapping`. It selects/reshapes JSON; it
  does not evaluate Python comparisons.
Rule of thumb: "which branch?" → this module; "pull/reshape these fields" → JMESPath.

Expressions here are sandboxed with RestrictedPython: no imports, no attribute escapes,
only a small set of safe builtins, plus explicit `state` / `context` dicts.
"""

from __future__ import annotations

from typing import Any

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter

_SAFE_NAMES: dict[str, Any] = {
    "len": len, "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "any": any, "all": all, "sorted": sorted, "str": str, "int": int, "float": float,
    "bool": bool, "list": list, "dict": dict, "set": set, "tuple": tuple,
    "True": True, "False": False, "None": None,
}


class ExpressionError(ValueError):
    """Raised when an expression fails to compile or evaluate."""


def eval_expression(expr: str, state: dict | None = None, context: dict | None = None) -> Any:
    state = dict(state or {})
    context = dict(context or {})
    try:
        code = compile_restricted(expr, "<forge-expression>", "eval")
    except SyntaxError as e:
        raise ExpressionError(f"Invalid expression {expr!r}: {e}") from e

    env: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "_getitem_": default_guarded_getitem,
        "_getiter_": default_guarded_getiter,
        **_SAFE_NAMES,
        **state,  # state keys as bare names
        "state": state,
        "context": context,
        "ctx": context,
    }
    try:
        return eval(code, env, {})  # noqa: S307 - sandboxed by RestrictedPython
    except Exception as e:  # noqa: BLE001 - surface any eval failure as ExpressionError
        raise ExpressionError(f"Failed to evaluate {expr!r}: {type(e).__name__}: {e}") from e


def eval_truthy(expr: str, state: dict | None = None, context: dict | None = None) -> bool:
    return bool(eval_expression(expr, state, context))
