"""Dispatch a tool config to a runnable StructuredTool by `kind`.

Beyond dispatch, this module hosts the cross-cutting guards that must hold for EVERY tool kind,
not just REST: the server-side entitlement gate (deny a tool independently of the LLM) and the
reliability controls the tool schema exposes for all kinds (rate_limit / cache / retry). REST
implements its own richer, HTTP-aware versions of these inside execute_rest; graphql/sql/code get
them here through a shared wrapper, so the UI's Reliability section is not a silent no-op for them
and the entitlement guarantee is not REST-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json as _json
import random
import time
from typing import Any

from forge.tools.builtin import build_builtin_tool
from forge.tools.code import build_code_tool
from forge.tools.graphql import build_graphql_tool
from forge.tools.rest import _resolve_retry, _should_retry, build_rest_tool
from forge.tools.sql import build_sql_tool
from forge.util.ratelimit import rate_limiter

# Process-global cache for the shared wrapper (graphql/sql/code). Partitioned by tenant/project +
# a fingerprint of the per-run context (run_context + end_user), exactly like rest._cache_key, so
# a per-user-authenticated tool can't serve one caller's private result to another.
_WRAP_CACHE: dict[str, tuple[float, Any]] = {}
_WRAP_CACHE_MAX = 5000


def entitlement_denial(cfg: dict, ctx) -> str | None:
    """Server-side entitlement gate (Feature 3b), shared by every non-REST kind (REST keeps an
    identical inline copy). Returns a corrective message to hand the model when the run's end_user
    lacks the entitlements the tool declares (config.required_entitlements), else None. This denies
    independently of the LLM - the model cannot talk its way past it."""
    required = cfg.get("required_entitlements") or []
    if required and getattr(ctx, "has_entitlements", None) and not ctx.has_entitlements(required):
        return f"Not permitted: this action requires {required}, which the current user is not entitled to."
    return None


def _accepts_runtime(fn) -> bool:
    """Whether the inner coroutine takes an injectable `runtime` (graphql does; sql/code don't)."""
    try:
        return "runtime" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return False


def _context_fingerprint(ctx) -> str:
    payload = {
        "run_context": getattr(ctx, "run_context", None) or {},
        "end_user": getattr(ctx, "end_user", None),
    }
    return hashlib.sha256(_json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _wrap_reliability(inner, cfg: dict, ctx, *, idempotent: bool):
    """Wrap a materialized non-REST tool with the shared entitlement gate + reliability controls
    (rate_limit / cache / retry) drawn from the same tool config REST honors.

    The wrapper calls the inner tool's RAW coroutine directly, forwarding the LangGraph-injected
    `runtime` only when the inner declares it - so graphql keeps its per-run context/streaming while
    sql/code (which take no runtime) are unaffected. Outer arg validation + runtime injection work
    exactly as in build_rest_tool (bare ToolRuntime annotation, explicit args_schema)."""
    from langchain.tools import ToolRuntime
    from langchain_core.tools import StructuredTool

    inner_coro = inner.coroutine
    pass_runtime = _accepts_runtime(inner_coro)
    name = cfg.get("name", "tool")
    kind = cfg.get("kind", "tool")
    tenant_id = getattr(ctx, "tenant_id", "") or ""
    project_id = getattr(ctx, "project_id", "") or ""

    rl = (cfg.get("rate_limit") or {}).get("per_minute")
    ttl = (cfg.get("cache") or {}).get("ttl_seconds", 0) or 0
    max_retries, retry_cfg, retry_types, retry_5xx = _resolve_retry(cfg)
    # Idempotency for retry gating: sql (read-only) and code (pure, no IO) are safe to retry, so
    # they present as a GET; graphql is POST-shaped (it may be a mutation), so it retries only when
    # the tool explicitly opts in via retry.retry_non_idempotent - never double-applying a mutation.
    method = "GET" if idempotent else "POST"

    async def _wrapped(runtime: ToolRuntime = None, **kwargs):  # type: ignore[assignment]
        # 1) Entitlement gate FIRST - deny before any network/DB work or cache read.
        denial = entitlement_denial(cfg, ctx)
        if denial:
            return denial
        # 2) Per-tool rate limit, scoped per tenant (mirrors execute_rest).
        if rl and not rate_limiter.allow(f"tool:{tenant_id}:{name}", rate=int(rl), per=60):
            raise RuntimeError(f"tool {name!r} exceeded its rate limit ({rl}/min)")
        # 3) Response cache (opt-in via cache.ttl_seconds), tenant/user-partitioned.
        cache_key = None
        if ttl:
            cache_key = "|".join([
                tenant_id, project_id, kind, name,
                _json.dumps(kwargs, sort_keys=True, default=str), _context_fingerprint(ctx),
            ])
            hit = _WRAP_CACHE.get(cache_key)
            if hit and (time.monotonic() - hit[0]) <= ttl:
                return hit[1]

        async def _call_inner():
            return await (inner_coro(runtime=runtime, **kwargs) if pass_runtime else inner_coro(**kwargs))

        # 4) Retry loop (opt-in; transient/5xx classification + idempotency gate from rest.py).
        attempt = 0
        while True:
            try:
                result = await _call_inner()
                break
            except Exception as e:  # noqa: BLE001 - retry classification below
                if attempt >= max_retries or not _should_retry(e, retry_types, retry_5xx, method, retry_cfg):
                    raise
                delay = min(
                    float(retry_cfg.get("max_delay", 60.0)),
                    float(retry_cfg.get("initial_delay", 1.0)) * (float(retry_cfg.get("backoff_factor", 2.0)) ** attempt),
                )
                if retry_cfg.get("jitter", True):
                    delay *= 0.5 + random.random()
                await asyncio.sleep(delay)
                attempt += 1

        if cache_key is not None:
            if len(_WRAP_CACHE) > _WRAP_CACHE_MAX:
                _WRAP_CACHE.clear()
            _WRAP_CACHE[cache_key] = (time.monotonic(), result)
        return result

    return StructuredTool.from_function(
        coroutine=_wrapped, name=inner.name, description=inner.description, args_schema=inner.args_schema,
    )


def materialize_tool(cfg: dict, ctx):
    kind = cfg.get("kind")
    if kind == "rest_api":
        # REST self-applies the entitlement gate + HTTP-aware reliability inside execute_rest.
        return build_rest_tool(cfg, ctx)
    if kind == "graphql":
        return _wrap_reliability(build_graphql_tool(cfg, ctx), cfg, ctx, idempotent=False)
    if kind == "builtin":
        # First-party safe tools (time/calculator/web_fetch/knowledge/memory); no per-tool
        # entitlement/reliability config surface, and some are sync (func=), so not wrapped.
        return build_builtin_tool(cfg, ctx)
    if kind == "code":
        return _wrap_reliability(build_code_tool(cfg, ctx), cfg, ctx, idempotent=True)
    if kind == "sql":
        return _wrap_reliability(build_sql_tool(cfg, ctx), cfg, ctx, idempotent=True)
    if kind == "mcp":
        # MCP tools are discovered/loaded ASYNCHRONOUSLY by the runtime assembler (load_mcp_tool),
        # not on this sync path. Return None (a deferred marker) rather than hard-raising, so a
        # saved mcp tool validates/creates and the sync registry simply skips it (tools_for filters
        # None) until the async loader populates the real tool - a raise here would abort the whole
        # tool-registry build for the project.
        return None
    raise ValueError(f"Unknown tool kind {kind!r}")
