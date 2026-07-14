"""Middleware-Stack Compiler (Doc 2 §8) - the engine of "limitless customization".

An agent node's `middleware: [{type, config, enabled}]` list compiles to a concrete
`list[AgentMiddleware]`. Prebuilt builders wrap LangChain's catalog (signatures
validated against langchain 1.3.4). The custom/advanced builders generate middleware
from declarative rules so non-coders get power without writing code.

Add a middleware = add a `MW_BUILDERS` entry (+ a `config_schemas` entry in
schemas/middleware.json + a `category-map` entry). It then appears everywhere.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
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
    hook_config,
)
from langchain.agents.middleware.types import PrivateStateAttr
from langgraph.channels.untracked_value import UntrackedValue

from forge.engine.context import CompileContext
from forge.engine.expressions import eval_truthy
from forge.engine.models import resolve_model

log = logging.getLogger("forge.middleware")

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
    kw = _pick(c, ["max_retries", "on_failure", "backoff_factor", "initial_delay", "max_delay", "jitter"])
    # Pass retry_on through like _tool_retry does - it was dropped before, so "retry only on
    # timeouts/http errors" configs silently retried on ANY exception (audit F5).
    retry_on = _retry_exceptions(c.get("retry_on") or [])
    if retry_on:
        kw["retry_on"] = retry_on
    return ModelRetryMiddleware(**kw)


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


class _DynamicModelByStateMiddleware(AgentMiddleware):
    """Switch the model at runtime by a state expression. Implemented as a class with BOTH the
    sync and async wrap hooks: the previous `@wrap_model_call`-on-a-sync-function version only
    provided the sync path, so it raised NotImplementedError under astream/ainvoke (the runtime
    path) - it never actually worked live. Wiring the agent-node `dynamic_model` field depends
    on this being async-safe (audit F9)."""

    def __init__(self, rules: list[dict], default: Any, ctx: CompileContext):
        super().__init__()
        self._rules = rules or []
        self._default = default
        self._ctx = ctx

    def _apply(self, request):
        chosen = self._default
        for r in self._rules:
            try:
                if eval_truthy(r["when"], dict(request.state or {})):
                    chosen = r["use"]
                    break
            except Exception:  # noqa: BLE001 - a bad rule shouldn't kill the run
                continue
        return request.override(model=resolve_model(chosen, self._ctx)) if chosen else request

    def wrap_model_call(self, request, handler):  # type: ignore[no-untyped-def]
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler):  # type: ignore[no-untyped-def]
        return await handler(self._apply(request))


def _dynamic_model_by_state(c: dict, ctx: CompileContext) -> AgentMiddleware:
    return _DynamicModelByStateMiddleware(c.get("rules", []), c.get("default"), ctx)


class _ToolFilterByContextMiddleware(AgentMiddleware):
    """Show/hide tools at runtime based on the run's context (auth state, role, flags). Same
    sync+async fix as _DynamicModelByStateMiddleware - it was sync-only and thus a no-op-then-
    crash under async execution."""

    def __init__(self, expose_when: dict, gated: set[str]):
        super().__init__()
        self._expose_when = expose_when
        self._gated = gated

    def _apply(self, request):
        rt_ctx = getattr(getattr(request, "runtime", None), "context", None) or {}
        allowed = _context_matches(self._expose_when, rt_ctx if isinstance(rt_ctx, dict) else {})
        if not allowed and self._gated:
            kept = [t for t in (request.tools or []) if getattr(t, "name", None) not in self._gated]
            return request.override(tools=kept)
        return request

    def wrap_model_call(self, request, handler):  # type: ignore[no-untyped-def]
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler):  # type: ignore[no-untyped-def]
        return await handler(self._apply(request))


def _tool_filter_by_context(c: dict, ctx: CompileContext) -> AgentMiddleware:
    return _ToolFilterByContextMiddleware(c.get("expose_when", {}), set(c.get("tools", [])))


_GUARDRAIL_REDACTION = "[redacted]"


class _GuardrailRegexMiddleware(AgentMiddleware):
    """Regex content guardrail honoring `apply_to` (input/output/both) and all three actions
    (block/redact/flag) - previously only the OUTPUT was scanned and only `block` did anything;
    `apply_to`, `redact` and `flag` were silently ignored (audit F4).

    - block:  replace the offending message with a fixed notice (existing behavior kept).
    - redact: mask each matched span with `[redacted]`, leaving the rest of the message intact.
    - flag:   keep the content but tag `additional_kwargs['guardrail_flagged']` and log it.

    Scanning input happens in before_model (so redaction is applied before the model sees it);
    scanning output happens in after_model (the model's reply)."""

    def __init__(self, patterns: list[str], on_match: str = "block", apply_to: str = "output"):
        super().__init__()
        self._patterns = [re.compile(p) for p in patterns]
        self._mode = on_match
        self._apply_to = apply_to

    def _rewrite(self, msg: Any):
        """Return a replacement message if a pattern matches under the mode, else None."""
        text = _msg_text(msg)
        if not self._patterns or not any(p.search(text) for p in self._patterns):
            return None
        from langchain_core.messages import AIMessage, HumanMessage

        is_ai = getattr(msg, "type", None) == "ai"
        make = AIMessage if is_ai else HumanMessage
        if self._mode == "block":
            return make(content="[blocked by content guardrail]")
        if self._mode == "redact":
            redacted = text
            for p in self._patterns:
                redacted = p.sub(_GUARDRAIL_REDACTION, redacted)
            return make(content=redacted)
        # flag: keep content, mark it for review (visible in the transcript / trace) + log.
        ak = dict(getattr(msg, "additional_kwargs", {}) or {})
        ak["guardrail_flagged"] = True
        log.warning("guardrail_regex flagged a message (a pattern matched)")
        return make(content=text, additional_kwargs=ak)

    def _scan(self, state: dict, which: str):
        from langchain_core.messages import RemoveMessage

        msgs = state.get("messages") or []
        want = ("human", "user") if which == "input" else ("ai",)
        target = next((m for m in reversed(msgs) if getattr(m, "type", None) in want), None)
        if target is None:
            return None
        repl = self._rewrite(target)
        if repl is None:
            return None
        tid = getattr(target, "id", None)
        return {"messages": [RemoveMessage(id=tid), repl] if tid else [repl]}

    def before_model(self, state, runtime=None):  # type: ignore[no-untyped-def]
        if self._apply_to in ("input", "both"):
            return self._scan(state, "input")
        return None

    def after_model(self, state, runtime=None):  # type: ignore[no-untyped-def]
        if self._apply_to in ("output", "both"):
            return self._scan(state, "output")
        return None


def _guardrail_regex(c: dict, ctx: CompileContext) -> AgentMiddleware:
    return _GuardrailRegexMiddleware(
        patterns=c.get("patterns", []),
        on_match=c.get("on_match", "block"),
        apply_to=c.get("apply_to", "output"),
    )


class _TenantBudgetState(AgentState):
    # Run-scoped token tally: UntrackedValue channels are NOT checkpointed, so this resets at
    # the start of every run (invocation) - giving true per-RUN scoping, unlike the old code
    # that summed usage over the whole persisted thread (audit F3). PrivateStateAttr keeps it
    # out of the agent's input/output schema so it never leaks to the workflow state.
    _forge_run_tokens: NotRequired[Annotated[int, UntrackedValue, PrivateStateAttr]]
    # Thread-scoped USD tally: a normal (checkpointed) channel, so it accumulates across the
    # runs of a thread - matching `max_usd_per_thread`.
    _forge_thread_cost_usd: NotRequired[Annotated[float, PrivateStateAttr]]


class _TenantBudgetMiddleware(AgentMiddleware):
    """Stop the run/thread when accumulated tokens or USD exceed a cap.

    - `max_tokens_per_run` is now scoped to the current RUN (was: whole persisted thread).
    - `max_usd_per_thread` is implemented via span-style pricing (forge.tracing.pricing.price)
      accumulated across the thread - previously the field was accepted and ignored (F3).
    Cost is only priced when a USD cap is set (keeps the token-only path free of lookups)."""

    state_schema = _TenantBudgetState  # type: ignore[assignment]

    def __init__(self, max_tokens_per_run: int | None, max_usd_per_thread: float | None, on_exceed: str = "end"):
        super().__init__()
        self._max_tokens = max_tokens_per_run
        self._max_usd = max_usd_per_thread
        self._on_exceed = on_exceed

    def _exceeded(self, reason: str):
        if self._on_exceed == "error":
            raise RuntimeError(f"Tenant budget exceeded: {reason}")
        from langchain_core.messages import AIMessage

        return {"jump_to": "end", "messages": [AIMessage(content=f"[budget] run stopped: {reason}")]}

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime=None):  # type: ignore[no-untyped-def]
        run_tokens = state.get("_forge_run_tokens", 0) or 0
        thread_cost = state.get("_forge_thread_cost_usd", 0.0) or 0.0
        if self._max_tokens and run_tokens >= self._max_tokens:
            return self._exceeded(f"{run_tokens} >= {self._max_tokens} tokens (this run)")
        if self._max_usd and thread_cost >= self._max_usd:
            return self._exceeded(f"${thread_cost:.4f} >= ${self._max_usd} (this thread)")
        return None

    def after_model(self, state, runtime=None):  # type: ignore[no-untyped-def]
        msgs = state.get("messages") or []
        if not msgs:
            return None
        last = msgs[-1]
        usage = getattr(last, "usage_metadata", None) or (
            last.get("usage_metadata") if isinstance(last, dict) else None
        )
        if not usage:
            return None
        in_tok = usage.get("input_tokens", 0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        total = usage.get("total_tokens", in_tok + out_tok) or 0
        update: dict[str, Any] = {"_forge_run_tokens": (state.get("_forge_run_tokens", 0) or 0) + total}
        if self._max_usd:
            from forge.tracing.pricing import price

            rm = getattr(last, "response_metadata", None) or {}
            model_name = rm.get("model_name") or rm.get("model")
            details = usage.get("input_token_details") or {}
            cost = price(
                model_name, in_tok, out_tok,
                cache_read_tokens=details.get("cache_read", 0) or 0,
                cache_creation_tokens=details.get("cache_creation", 0) or 0,
            )
            update["_forge_thread_cost_usd"] = (state.get("_forge_thread_cost_usd", 0.0) or 0.0) + cost
        return update


def _tenant_budget(c: dict, ctx: CompileContext) -> AgentMiddleware:
    return _TenantBudgetMiddleware(
        max_tokens_per_run=c.get("max_tokens_per_run"),
        max_usd_per_thread=c.get("max_usd_per_thread"),
        on_exceed=c.get("on_exceed", "end"),
    )


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
