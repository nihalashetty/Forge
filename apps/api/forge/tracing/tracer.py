"""ForgeTracer - a LangChain callback handler that records spans for a run.

Captures LLM spans (model, token usage, cost) and tool/chain spans, nested by
run_id/parent_run_id. Spans are collected in memory for the run; the RunService
persists them to `traces`/`spans` at the end (a Redis sink + flush worker is the
prod swap). Span attributes follow OTEL GenAI conventions where natural.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from forge.config import settings
from forge.tracing import tool_io
from forge.tracing.pricing import price

# The run's active tracer, bound per asyncio context when a ForgeTracer is constructed.
# An embedding call deep in RAG/memory is far from the tracer that owns the run's spans
# (same rationale as tool_io); it reaches back through this to attribute embedding latency
# + cost to the run's trace, WITHOUT threading a handle through every call site.
_ACTIVE_TRACER: ContextVar[Any] = ContextVar("forge_active_tracer", default=None)


@contextmanager
def embedding_span(model: str | None, *, n_texts: int = 1, input_tokens: int = 0, name: str | None = None):
    """Time an embedding call as a `kind="embedding"` span on the active run's tracer (priced
    via the embedding rates in pricing.price). A no-op when no run tracer is bound, so call
    sites (knowledge/memory RAG) can wrap embed calls unconditionally. See ForgeTracer.embedding_span."""
    tr = _ACTIVE_TRACER.get()
    if tr is None:
        yield
        return
    with tr.embedding_span(model, n_texts=n_texts, input_tokens=input_tokens, name=name):
        yield


def _stringify(output: Any) -> Any:
    """Reduce a non-REST tool's return value to something storable. A str/dict/list is kept
    as-is (rendered structurally in the UI); anything else (e.g. a ToolMessage) becomes its
    `.content` or `str()`."""
    if isinstance(output, (str, int, float, bool, dict, list)) or output is None:
        return output
    return getattr(output, "content", None) or str(output)


@dataclass
class SpanRecord:
    id: str
    parent_id: str | None
    name: str
    kind: str  # llm|tool|chain|retriever|agent|node
    start: float
    end: float | None = None
    # Wall-clock (unix epoch seconds) start/end, for ABSOLUTE timestamps: OTel export and
    # accurate waterfall positioning. `start`/`end` stay MONOTONIC (correct for latency_ms and
    # immune to clock adjustments); these carry real time so exported spans don't land at ~1970.
    start_wall: float = 0.0
    end_wall: float | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    attributes: dict = field(default_factory=dict)
    # Tool spans only: what the agent sent the tool (LLM args / framed REST request) and what
    # came back (response or error). Populated from the tool-I/O context var; see forge.tracing.tool_io.
    input: Any = None
    output: Any = None

    @property
    def latency_ms(self) -> int:
        if self.end is None:
            return 0
        return int((self.end - self.start) * 1000)


def _usage(response: Any) -> tuple[int, int, int, int]:
    """(input, output, cache_read, cache_creation) tokens. Cache tiers come from
    usage_metadata.input_token_details (Anthropic/OpenAI prompt caching) so cost accounting
    can bill cache reads/writes at their discounted/premium rates instead of full input."""
    try:
        msg = response.generations[0][0].message
        um = getattr(msg, "usage_metadata", None)
        if um:
            details = um.get("input_token_details") or {}
            cache_read = int(details.get("cache_read", 0) or 0)
            cache_creation = int(details.get("cache_creation", 0) or 0)
            return int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0)), cache_read, cache_creation
    except Exception:  # noqa: BLE001
        pass
    lo = getattr(response, "llm_output", None) or {}
    usage = lo.get("token_usage") or lo.get("usage") or {}
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), 0, 0


# Exact names of internal LCEL/LangGraph runnables that add depth without meaning.
_INTERNAL_CHAINS = frozenset({
    "chain", "LangGraph", "Pregel", "PregelLoop", "PregelNode",
    "RunnableSequence", "RunnableLambda", "RunnableParallel", "RunnableAssign",
    "RunnableWithFallbacks", "RunnableBinding", "RunnableEach", "RunnableGenerator",
    "RunnableMap", "RunnableCallable", "_route", "_control_branch",
})


def _is_internal_chain(name: str) -> bool:
    """True for unnamed/internal wrapper runnables we should NOT open a span for; keep the
    named graph nodes, agents, tools, and models (finding F5)."""
    if not name or name in _INTERNAL_CHAINS:
        return True
    # LCEL wrappers (Runnable*), channel writers (ChannelWrite/Read), and dunder/underscore
    # internals (__start__, __end__, _write) are structural noise.
    return name.startswith(("Runnable", "Channel", "_"))


class ForgeTracer(BaseCallbackHandler):
    """Sync callback handler (LangChain runs it safely inside async astream)."""

    raise_error = False

    def __init__(self) -> None:
        self.spans: dict[str, SpanRecord] = {}
        self._order: list[str] = []
        # Internal chains we chose NOT to open (finding F5): child run_id -> its nearest
        # REAL ancestor span id, so a child of a skipped chain still nests under a live span
        # instead of dangling as an orphan.
        self._skipped: dict[str, str | None] = {}
        # Bind as the active tracer for this asyncio context (for embedding_span()).
        _ACTIVE_TRACER.set(self)

    # --- helpers ---
    def _resolve_parent(self, parent) -> str | None:
        """Walk up through skipped chains to the nearest real ancestor span (or None)."""
        pid = str(parent) if parent else None
        seen: set[str] = set()
        while pid is not None and pid in self._skipped and pid not in seen:
            seen.add(pid)
            pid = self._skipped[pid]
        return pid

    def _open(self, run_id, parent, name, kind, **attrs) -> None:
        sid = str(run_id)
        # `model` is a first-class SpanRecord field (cost pricing reads it on close);
        # pop it out of attrs so it lands on the field, not the attributes bag.
        model = attrs.pop("model", None)
        self.spans[sid] = SpanRecord(
            id=sid, parent_id=self._resolve_parent(parent), name=name, kind=kind,
            start=time.monotonic(), start_wall=time.time(), model=model, attributes=attrs,
        )
        self._order.append(sid)

    def _close(self, run_id, **fields) -> None:
        sp = self.spans.get(str(run_id))
        if not sp:
            return
        sp.end = time.monotonic()
        sp.end_wall = time.time()
        for k, v in fields.items():
            setattr(sp, k, v)

    # --- LLM / chat model ---
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kw):  # noqa: D401
        model = self._model_name(serialized, kw)
        self._open(run_id, parent_run_id, f"model · {model}" if model else "model", "llm", model=model)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kw):
        model = self._model_name(serialized, kw)
        self._open(run_id, parent_run_id, f"model · {model}" if model else "model", "llm", model=model)

    def on_llm_end(self, response, *, run_id, **kw):
        in_tok, out_tok, cache_read, cache_creation = _usage(response)
        sp = self.spans.get(str(run_id))
        model = sp.model if sp else None
        self._close(
            run_id, input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=price(model, in_tok, out_tok, cache_read_tokens=cache_read, cache_creation_tokens=cache_creation),
        )

    def on_llm_error(self, error, *, run_id, **kw):
        self._close(run_id, error=str(error))

    # --- tools ---
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name", "tool")
        self._open(run_id, parent_run_id, f"tool · {name}", "tool", tool=name)
        # Provisional input = the raw LLM tool args (safe: never carries server-injected ctx
        # secrets). A REST tool overrides this with the framed request in _tool_io below.
        if settings.trace_tool_io:
            self._set(run_id, input=tool_io.clip(input_str))

    def on_tool_end(self, output, *, run_id, **kw):
        self._tool_io(run_id, fallback_output=output)
        self._close(run_id)

    def on_tool_error(self, error, *, run_id, **kw):
        self._tool_io(run_id)
        self._close(run_id, error=str(error))

    def _tool_io(self, run_id, *, fallback_output=None) -> None:
        """Merge the tool's captured framed I/O (set by the tool via forge.tracing.tool_io)
        onto its span. REST tools record a rich {request}/{response}; other tools fall back to
        the raw return value. The name guard rejects a stale record left by an earlier tool."""
        if not settings.trace_tool_io:
            return
        sp = self.spans.get(str(run_id))
        rec = tool_io.take_tool_io()
        tool_io.clear_tool_io()
        if sp and rec and rec.get("name") == sp.attributes.get("tool"):
            sp.input = rec.get("input")
            sp.output = rec.get("output")
        elif sp and fallback_output is not None:
            sp.output = tool_io.clip(_stringify(fallback_output))

    def _set(self, run_id, **fields) -> None:
        sp = self.spans.get(str(run_id))
        if sp:
            for k, v in fields.items():
                setattr(sp, k, v)

    # --- chains / graph nodes ---
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name") or kw.get("name") or "chain"
        # Actually filter the noisy internal runnables (finding F5): LangGraph/LCEL wrap every
        # node in RunnableSequence/Lambda/ChannelWrite/etc. spans that bury the real node/agent/
        # tool/model spans. Record the skip so descendants re-parent onto the nearest real span.
        if _is_internal_chain(name):
            self._skipped[str(run_id)] = self._resolve_parent(parent_run_id)
            return
        self._open(run_id, parent_run_id, name, "chain")

    def on_chain_end(self, outputs, *, run_id, **kw):
        self._close(run_id)

    def on_chain_error(self, error, *, run_id, **kw):
        self._close(run_id, error=str(error))

    # --- retrievers (RAG / vector search) ---
    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name") or kw.get("name") or "retriever"
        label = "retriever" if name in ("retriever", "Retriever") else f"retriever · {name}"
        self._open(run_id, parent_run_id, label, "retriever")
        if settings.trace_tool_io and query is not None:
            self._set(run_id, input=tool_io.clip(query))

    def on_retriever_end(self, documents, *, run_id, **kw):
        docs = list(documents) if isinstance(documents, (list, tuple)) else []
        sp = self.spans.get(str(run_id))
        if sp is not None:
            sp.attributes["docs"] = len(docs)
            if settings.trace_tool_io:
                previews = []
                for d in docs[:4]:
                    text = getattr(d, "page_content", None)
                    if text is None and isinstance(d, dict):
                        text = d.get("page_content") or d.get("text")
                    previews.append(str(text if text is not None else d)[:400])
                sp.output = tool_io.clip(previews)
        self._close(run_id)

    def on_retriever_error(self, error, *, run_id, **kw):
        self._close(run_id, error=str(error))

    # --- embeddings (RAG / memory) ---
    @contextmanager
    def embedding_span(self, model: str | None, *, n_texts: int = 1, input_tokens: int = 0, name: str | None = None):
        """Open a `kind="embedding"` span timing an embed call and pricing it via the
        embedding rates in pricing.price (embeddings have no output tokens). Use the
        module-level `embedding_span` from call sites so it degrades to a no-op off-run."""
        sid = str(uuid.uuid4())
        self.spans[sid] = SpanRecord(
            id=sid, parent_id=None, name=name or (f"embedding · {model}" if model else "embedding"),
            kind="embedding", start=time.monotonic(), start_wall=time.time(), model=model,
        )
        self._order.append(sid)
        try:
            yield
        finally:
            sp = self.spans[sid]
            sp.end = time.monotonic()
            sp.end_wall = time.time()
            sp.input_tokens = int(input_tokens)
            sp.cost_usd = price(model, int(input_tokens), 0)
            sp.attributes["n_texts"] = int(n_texts)

    @staticmethod
    def _model_name(serialized, kw) -> str | None:
        inv = kw.get("invocation_params") or {}
        return inv.get("model") or inv.get("model_name") or (serialized or {}).get("name")

    # --- rollups ---
    def totals(self) -> tuple[int, float]:
        tokens = sum(s.input_tokens + s.output_tokens for s in self.spans.values())
        cost = sum(s.cost_usd for s in self.spans.values())
        return tokens, round(cost, 6)

    def ordered(self) -> list[SpanRecord]:
        return [self.spans[sid] for sid in self._order if sid in self.spans]
