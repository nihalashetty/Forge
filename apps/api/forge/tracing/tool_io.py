"""Per-tool-call I/O capture for traces.

A tool runs deep inside the compiled agent subgraph, far from the `ForgeTracer`
that owns the span. Rather than thread a span id all the way down, a tool records
its framed request/response into a `ContextVar` and the tracer reads it back when
it closes the tool span (`on_tool_end` / `on_tool_error`). The two run in the same
asyncio task, and LangChain dispatches sync callbacks through `copy_context().run`,
so the value the tool set is visible to the callback.

This is why the record carries the tool `name`: a later NON-REST tool call in the
same task could otherwise observe a stale REST record left in the context. The
tracer only applies a record whose name matches the span it is closing.

Redaction and clipping are policy: `trace_tool_io_redact` masks sensitive
header/cookie values; `trace_tool_io_max_chars` bounds each stored field so a big
body can't bloat the `spans` table.
"""

from __future__ import annotations

import json as _json
from contextvars import ContextVar
from typing import Any

from forge.config import settings

# The current tool call's captured I/O, keyed implicitly by asyncio task/context.
# Shape: {"name": str, "input": {...}, "output": {...}}.
_TOOL_IO: ContextVar[dict | None] = ContextVar("forge_tool_io", default=None)

# Header/cookie names whose VALUES are secrets. Matched case-insensitively; a name
# that merely CONTAINS one of these (e.g. "X-CSRF-Token") is treated as sensitive.
_SENSITIVE = ("authorization", "cookie", "set-cookie", "csrf", "xsrf", "token", "secret",
              "api-key", "apikey", "x-api-key", "password", "session")


def clear_tool_io() -> None:
    _TOOL_IO.set(None)


def take_tool_io() -> dict | None:
    """Read the current record (the tracer resets it via clear_tool_io after use)."""
    return _TOOL_IO.get()


def set_tool_io(name: str, *, request: dict, response: dict) -> None:
    """Record one tool call's framed I/O. No-op when tool-I/O capture is disabled."""
    if not settings.trace_tool_io:
        return
    _TOOL_IO.set({"name": name, "input": request, "output": response})


def _is_sensitive(key: str) -> bool:
    k = (key or "").lower()
    return any(s in k for s in _SENSITIVE)


def _mask(value: Any) -> str:
    s = value if isinstance(value, str) else str(value)
    return f"••• ({len(s)} chars)" if s else "••• (empty)"


def redact_headers(headers: dict | None, *, mask_all: bool = False) -> dict:
    """Mask sensitive header/query values when redaction is on; else pass through.

    Presence + length is preserved so you can still tell a cookie/CSRF was attached
    without persisting the secret itself. `mask_all` masks EVERY value (use for cookies -
    a cookie value is a credential regardless of its key name, e.g. `sid`).
    """
    if not headers:
        return {}
    if not settings.trace_tool_io_redact:
        return {str(k): v for k, v in headers.items()}
    return {str(k): (_mask(v) if (mask_all or _is_sensitive(str(k))) else v) for k, v in headers.items()}


def clip(value: Any) -> Any:
    """Bound a stored value so a large body/response can't bloat the spans table.

    JSON-serializable values under the cap are stored as-is (so the UI renders them
    structurally); oversized ones become truncated JSON text.
    """
    cap = settings.trace_tool_io_max_chars
    if not cap or cap <= 0:
        return value
    try:
        s = _json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return str(value)[:cap]
    return value if len(s) <= cap else s[:cap] + "… (truncated)"
