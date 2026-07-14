"""ForgeTracer - a LangChain callback handler that records spans for a run.

Captures LLM spans (model, token usage, cost) and tool/chain spans, nested by
run_id/parent_run_id. Spans are collected in memory for the run; the RunService
persists them to `traces`/`spans` at the end (a Redis sink + flush worker is the
prod swap). Span attributes follow OTEL GenAI conventions where natural.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from forge.config import settings
from forge.tracing import tool_io
from forge.tracing.pricing import price


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


class ForgeTracer(BaseCallbackHandler):
    """Sync callback handler (LangChain runs it safely inside async astream)."""

    raise_error = False

    def __init__(self) -> None:
        self.spans: dict[str, SpanRecord] = {}
        self._order: list[str] = []

    # --- helpers ---
    def _open(self, run_id, parent, name, kind, **attrs) -> None:
        sid = str(run_id)
        # `model` is a first-class SpanRecord field (cost pricing reads it on close);
        # pop it out of attrs so it lands on the field, not the attributes bag.
        model = attrs.pop("model", None)
        self.spans[sid] = SpanRecord(
            id=sid, parent_id=str(parent) if parent else None, name=name, kind=kind,
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
        # Skip the noisiest internal runnables; keep named nodes/agents.
        self._open(run_id, parent_run_id, name, "chain")

    def on_chain_end(self, outputs, *, run_id, **kw):
        self._close(run_id)

    def on_chain_error(self, error, *, run_id, **kw):
        self._close(run_id, error=str(error))

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
