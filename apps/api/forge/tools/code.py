"""Code tool - run a small user-authored Python function as an agent tool.

Sandboxed with RestrictedPython (AST-level: no dunder access, no eval/exec, guarded
item/attr access) plus an allowlisted importer and a bounded execution wait. This is
the right tool for "compute / reshape / glue" logic that REST/JMESPath can't express.

Security note: RestrictedPython prevents most escapes but does NOT bound CPU/memory or
truly kill a runaway thread. For untrusted multi-tenant code at scale, run via an
isolated executor (subprocess/container or the deep-agent sandbox backend); gate with
`FORGE_ENABLE_CODE_TOOLS`. The convention is: define `def main(**kwargs): return ...`
(or assign a top-level `result`).
"""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any

from RestrictedPython import compile_restricted, safe_builtins, utility_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    safer_getattr,
)

from forge.config import settings

# Modules a code tool may import - pure/standard, no IO or network.
_ALLOWED_IMPORTS = {
    "json", "math", "re", "datetime", "statistics", "random", "string",
    "itertools", "functools", "collections", "decimal", "base64", "hashlib", "uuid",
}


# Ceiling on a code tool's returned value so it can't hand the model a multi-megabyte blob (and
# can't be used to amplify memory). Wanted setting: `code_tool_max_result_chars` (default 100000);
# a module constant for now.
_MAX_RESULT_CHARS = 100_000


def _cap_result(result: Any) -> Any:
    """Return the result unchanged, or a small marker when its serialized size is over the cap."""
    try:
        s = result if isinstance(result, str) else _json.dumps(result, default=str)
    except Exception:  # noqa: BLE001 - unserializable -> fall back to repr for the size check
        s = str(result)
    if len(s) > _MAX_RESULT_CHARS:
        return {"error": "result_too_large", "chars": len(s), "limit": _MAX_RESULT_CHARS, "preview": s[:2000]}
    return result


def _guarded_import(name, *args, **kwargs):
    root = name.split(".")[0]
    if root not in _ALLOWED_IMPORTS:
        raise ImportError(f"import of {name!r} is not allowed in a code tool")
    return __import__(name, *args, **kwargs)


def _safe_globals() -> dict:
    builtins = dict(safe_builtins)
    builtins.update(utility_builtins)
    builtins["__import__"] = _guarded_import
    return {
        "__builtins__": builtins,
        "_getiter_": default_guarded_getiter,
        "_getitem_": default_guarded_getitem,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_getattr_": safer_getattr,
        "_write_": full_write_guard,
        "_print_": lambda *a, **k: None,
    }


class CodeToolError(RuntimeError):
    pass


def run_code(source: str, kwargs: dict[str, Any]) -> Any:
    """Compile + execute the source in a restricted namespace and return the result."""
    try:
        byte_code = compile_restricted(source, "<code-tool>", "exec")
    except SyntaxError as e:
        raise CodeToolError(f"compile error: {e}") from e
    ns = _safe_globals()
    try:
        exec(byte_code, ns)  # noqa: S102 - sandboxed by RestrictedPython
    except Exception as e:  # noqa: BLE001
        raise CodeToolError(f"load error: {type(e).__name__}: {e}") from e
    main = ns.get("main")
    try:
        result = main(**kwargs) if callable(main) else ns.get("result")
    except Exception as e:  # noqa: BLE001
        raise CodeToolError(f"runtime error: {type(e).__name__}: {e}") from e
    return result


async def execute_code(cfg: dict, kwargs: dict) -> Any:
    if not settings.enable_code_tools:
        raise CodeToolError("code tools are disabled (FORGE_ENABLE_CODE_TOOLS=false)")
    if cfg.get("language", "python") != "python":
        raise CodeToolError("only python code tools are supported")
    source = cfg.get("source") or ""
    timeout = float(cfg.get("timeout_seconds", 5))
    try:
        result = await asyncio.wait_for(asyncio.to_thread(run_code, source, kwargs), timeout=timeout)
    except TimeoutError as e:
        # RESIDUAL (documented limitation): wait_for abandons the awaited result cleanly, but the
        # worker THREAD cannot be forcibly killed in CPython - a runaway (e.g. `while True: pass`)
        # keeps running on the shared executor until it finishes on its own, tying up a thread.
        # Truly bounding CPU/threads needs an isolated executor (subprocess/container). Gate with
        # FORGE_ENABLE_CODE_TOOLS; do not enable untrusted code tools in a shared install.
        raise CodeToolError(f"code tool timed out after {timeout}s") from e
    return _cap_result(result)


def build_code_tool(cfg: dict, ctx):
    from langchain_core.tools import StructuredTool

    from forge.tools.rest import build_args_schema_from_jsonschema

    args_schema = build_args_schema_from_jsonschema(
        cfg.get("args_schema") or {}, name=f"{cfg.get('name', 'code')}_args"
    )

    async def _call(**kwargs):
        return await execute_code(cfg, kwargs)

    return StructuredTool.from_function(
        coroutine=_call, name=cfg["name"], description=cfg.get("description", ""), args_schema=args_schema,
    )
