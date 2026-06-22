"""Middleware-Stack Compiler (Doc 2 §8) - the engine of "limitless customization".

An agent node's `middleware: [{type, config, enabled}]` list compiles to a concrete
`list[AgentMiddleware]`. Prebuilt builders wrap LangChain's catalog (signatures
validated against langchain 1.3.4). The custom/advanced builders generate middleware
from declarative rules so non-coders get power without writing code.

Add a middleware = add a `MW_BUILDERS` entry (+ a `config_schemas` entry in
schemas/middleware.json + a `category-map` entry). It then appears everywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    HumanInTheLoopMiddleware,
    LLMToolEmulator,
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
    after_model,
    before_model,
    wrap_model_call,
)

from forge.engine.context import CompileContext
from forge.engine.expressions import eval_truthy
from forge.engine.models import resolve_model

Builder = Callable[[dict, CompileContext], AgentMiddleware]


# --- helpers ---------------------------------------------------------------


def _ctxsize(v: Any) -> Any:
    """JSON ContextSize (list) -> tuple; list-of-ContextSize -> list[tuple]."""
    if v is None:
        return None
    if isinstance(v, list) and v and isinstance(v[0], list):
        return [tuple(x) for x in v]
    if isinstance(v, (list, tuple)):
        return tuple(v)
    return v


def _pick(c: dict, keys: list[str]) -> dict:
    return {k: c[k] for k in keys if k in c and c[k] is not None}


def _context_matches(expose_when: dict, ctx_data: dict) -> bool:
    """All keys in expose_when must match (value-in-list or equality)."""
    for k, want in (expose_when or {}).items():
        got = ctx_data.get(k)
        if isinstance(want, list):
            if got not in want:
                return False
        elif got != want:
            return False
    return True


def _msg_text(msg: Any) -> str:
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
    if isinstance(content, list):
        return " ".join(
            (b.get("text", "") if isinstance(b, dict) else str(b)) for b in content
        )
    return content or ""


# --- prebuilt builders (signatures validated against langchain 1.3.4) ------


def _summarization(c: dict, ctx: CompileContext) -> AgentMiddleware:
    kw: dict[str, Any] = {}
    if c.get("trigger") is not None:
        kw["trigger"] = _ctxsize(c["trigger"])
    if c.get("keep") is not None:
        kw["keep"] = _ctxsize(c["keep"])
    if c.get("summary_prompt"):
        kw["summary_prompt"] = c["summary_prompt"]
    return SummarizationMiddleware(model=resolve_model(c.get("model"), ctx), **kw)


def _model_fallback(c: dict, ctx: CompileContext) -> AgentMiddleware:
    models = [resolve_model(m, ctx) for m in c["models"]]
    return ModelFallbackMiddleware(models[0], *models[1:])


def _pii(c: dict, ctx: CompileContext) -> AgentMiddleware:
    return PIIMiddleware(
        c["pii_type"],
        strategy=c.get("strategy", "redact"),
        detector=c.get("detector"),
        apply_to_input=c.get("apply_to_input", True),
        apply_to_output=c.get("apply_to_output", False),
        apply_to_tool_results=c.get("apply_to_tool_results", False),
    )


def _llm_tool_selector(c: dict, ctx: CompileContext) -> AgentMiddleware:
    kw = _pick(c, ["max_tools", "always_include"])
    return LLMToolSelectorMiddleware(model=resolve_model(c.get("model"), ctx), **kw)


# retry_on string names (from the JSON config) -> real exception types the lib expects.
# ("http_error"/"httpx"/"http" resolve lazily to httpx.HTTPError in _retry_exceptions.)
_RETRY_EXC_MAP: dict[str, type[BaseException]] = {
    "exception": Exception,
    "timeout": TimeoutError,
    "timeout_error": TimeoutError,
    "connection": ConnectionError,
    "connection_error": ConnectionError,
    "value_error": ValueError,
    "key_error": KeyError,
    "runtime_error": RuntimeError,
}


def _retry_exceptions(names: list[str]) -> tuple[type[BaseException], ...]:
    out: list[type[BaseException]] = []
    for n in names or []:
        key = str(n).strip().lower()
        if key in ("http_error", "httpx", "http"):
            import httpx

            out.append(httpx.HTTPError)
            continue
        exc = _RETRY_EXC_MAP.get(key)
        if exc is not None:
            out.append(exc)
    return tuple(out)


def _tool_retry(c: dict, ctx: CompileContext) -> AgentMiddleware:
    kw = _pick(c, ["max_retries", "tools", "on_failure", "backoff_factor", "initial_delay", "max_delay", "jitter"])
    retry_on = _retry_exceptions(c.get("retry_on") or [])
    if retry_on:
        kw["retry_on"] = retry_on
    return ToolRetryMiddleware(**kw)


def _model_retry(c: dict, ctx: CompileContext) -> AgentMiddleware:
    return ModelRetryMiddleware(
        **_pick(c, ["max_retries", "on_failure", "backoff_factor", "initial_delay", "max_delay", "jitter"])
    )


def _tool_emulator(c: dict, ctx: CompileContext) -> AgentMiddleware:
    kw: dict[str, Any] = {}
    if c.get("tools") is not None:
        kw["tools"] = c["tools"]
    if c.get("model"):
        kw["model"] = resolve_model(c["model"], ctx)
    return LLMToolEmulator(**kw)


def _context_editing(c: dict, ctx: CompileContext) -> AgentMiddleware:
    edits = [ClearToolUsesEdit(**e) for e in c.get("edits", [])] or [ClearToolUsesEdit()]
    return ContextEditingMiddleware(edits=edits)


def _anthropic_prompt_caching(c: dict, ctx: CompileContext) -> AgentMiddleware:
    # Provider-specific: lives in langchain-anthropic (install extra: providers).
    try:
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
    except ImportError as e:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "anthropic_prompt_caching needs `langchain-anthropic` "
            "(pip install -e '.[providers]')."
        ) from e
    return AnthropicPromptCachingMiddleware(**_pick(c, ["ttl"]))


def _openai_moderation(c: dict, ctx: CompileContext) -> AgentMiddleware:
    try:
        from langchain_openai.middleware import OpenAIModerationMiddleware
    except ImportError as e:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "openai_moderation needs `langchain-openai` (pip install -e '.[providers]')."
        ) from e
    return OpenAIModerationMiddleware(**_pick(c, ["apply_to_input", "apply_to_output"]))


# --- custom / advanced builders (declarative rules -> hooks) ---------------


def _dynamic_model_by_state(c: dict, ctx: CompileContext) -> AgentMiddleware:
    rules = c.get("rules", [])
    default = c.get("default")

    @wrap_model_call
    def _mw(request, handler):  # type: ignore[no-untyped-def]
        chosen = default
        for r in rules:
            try:
                if eval_truthy(r["when"], dict(request.state or {})):
                    chosen = r["use"]
                    break
            except Exception:  # noqa: BLE001 - a bad rule shouldn't kill the run
                continue
        if chosen:
            request = request.override(model=resolve_model(chosen, ctx))
        return handler(request)

    return _mw


def _tool_filter_by_context(c: dict, ctx: CompileContext) -> AgentMiddleware:
    expose_when = c.get("expose_when", {})
    gated = set(c.get("tools", []))

    @wrap_model_call
    def _mw(request, handler):  # type: ignore[no-untyped-def]
        rt_ctx = getattr(getattr(request, "runtime", None), "context", None) or {}
        allowed = _context_matches(expose_when, rt_ctx if isinstance(rt_ctx, dict) else {})
        if not allowed and gated:
            kept = [t for t in (request.tools or []) if getattr(t, "name", None) not in gated]
            request = request.override(tools=kept)
        return handler(request)

    return _mw


def _guardrail_regex(c: dict, ctx: CompileContext) -> AgentMiddleware:
    patterns = [re.compile(p) for p in c.get("patterns", [])]
    on_match = c.get("on_match", "block")

    @after_model
    def _mw(state, runtime=None):  # type: ignore[no-untyped-def]
        msgs = state.get("messages") or []
        if not msgs or not patterns:
            return None
        last = msgs[-1]
        text = _msg_text(last)
        if not any(p.search(text) for p in patterns):
            return None
        if on_match == "block":
            from langchain_core.messages import AIMessage, RemoveMessage

            # Actually REPLACE the offending reply (remove + substitute) so the
            # blocked content never reaches the transcript.
            replacement = AIMessage(content="[blocked by content guardrail]")
            last_id = getattr(last, "id", None)
            if last_id:
                return {"messages": [RemoveMessage(id=last_id), replacement]}
            return {"messages": [replacement]}
        # redact/flag are best-effort; full redaction is handled by PIIMiddleware.
        return None

    return _mw


def _tenant_budget(c: dict, ctx: CompileContext) -> AgentMiddleware:
    max_tokens = c.get("max_tokens_per_run")
    on_exceed = c.get("on_exceed", "end")

    @before_model
    def _mw(state, runtime=None):  # type: ignore[no-untyped-def]
        if not max_tokens:
            return None
        used = 0
        for m in state.get("messages") or []:
            usage = getattr(m, "usage_metadata", None) or (
                m.get("usage_metadata") if isinstance(m, dict) else None
            )
            if usage:
                used += usage.get("total_tokens", 0)
        if used >= max_tokens:
            if on_exceed == "error":
                raise RuntimeError(f"Tenant budget exceeded: {used} >= {max_tokens} tokens")
            return {"jump_to": "end"}
        return None

    return _mw


MW_BUILDERS: dict[str, Builder] = {
    "summarization": _summarization,
    "human_in_the_loop": lambda c, ctx: HumanInTheLoopMiddleware(interrupt_on=c["interrupt_on"]),
    "model_call_limit": lambda c, ctx: ModelCallLimitMiddleware(
        **_pick(c, ["thread_limit", "run_limit", "exit_behavior"])
    ),
    "tool_call_limit": lambda c, ctx: ToolCallLimitMiddleware(
        **_pick(c, ["tool_name", "thread_limit", "run_limit", "exit_behavior"])
    ),
    "model_fallback": _model_fallback,
    "pii": _pii,
    "todo": lambda c, ctx: TodoListMiddleware(**_pick(c, ["system_prompt", "tool_description"])),
    "llm_tool_selector": _llm_tool_selector,
    "tool_retry": _tool_retry,
    "model_retry": _model_retry,
    "tool_emulator": _tool_emulator,
    "context_editing": _context_editing,
    "anthropic_prompt_caching": _anthropic_prompt_caching,
    "openai_moderation": _openai_moderation,
    # custom / advanced
    # (request_signing was removed: it was a no-op stub - auth injection already
    # happens inside materialized REST tools via the AuthResolver.)
    "dynamic_model_by_state": _dynamic_model_by_state,
    "tool_filter_by_context": _tool_filter_by_context,
    "guardrail_regex": _guardrail_regex,
    "tenant_budget": _tenant_budget,
}


def build_middleware(stack: list[dict] | None, ctx: CompileContext) -> list[AgentMiddleware]:
    """Compile a middleware stack list into concrete middleware instances.

    Disabled entries are skipped. Unknown types raise (caught upstream by the
    validator, which reports them as field-level errors before compile).
    """
    out: list[AgentMiddleware] = []
    for m in stack or []:
        if m.get("enabled", True) is False:
            continue
        mtype = m.get("type")
        builder = MW_BUILDERS.get(mtype)
        if builder is None:
            raise ValueError(f"Unknown middleware type: {mtype!r}")
        out.append(builder(m.get("config") or {}, ctx))
    return out
