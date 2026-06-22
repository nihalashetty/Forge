"""Response projection - the primary token-cost lever (Doc 2 §10).

`project_response` cuts a raw API payload down to what the model actually needs,
*before* it becomes a ToolMessage. Three strategies, in priority order:

1. JMESPath projection (`projection_jmespath`) - most expressive (rename/reshape).
2. Field list (`fields[].include_in_llm`) - keep selected dotted paths.
3. Otherwise return the full payload unchanged.
"""

from __future__ import annotations

import json
from typing import Any

import jmespath


def get_path(data: Any, dotted: str) -> Any:
    """Resolve a dotted path like `data.totals.subtotal` (supports list[idx])."""
    cur = data
    for part in dotted.split("."):
        if cur is None:
            return None
        if part.isdigit() and isinstance(cur, (list, tuple)):
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def project_response(data: Any, response_cfg: dict | None) -> Any:
    """Apply the configured projection to a raw response payload."""
    if not response_cfg:
        return data

    expr = response_cfg.get("projection_jmespath")
    if expr:
        try:
            return jmespath.search(expr, data)
        except jmespath.exceptions.JMESPathError:
            # Bad expression at runtime: fall back to full payload rather than crash.
            return data

    fields = [f for f in response_cfg.get("fields", []) if f.get("include_in_llm", True)]
    if fields:
        return {f["path"]: get_path(data, f["path"]) for f in fields}

    return data


def cap_payload(value: Any, max_chars: int) -> Any:
    """Guard against an un-projected tool response blowing the model's context: if the
    serialized value exceeds `max_chars`, return a truncated string with a marker. Small
    values pass through unchanged so projected/structured results keep their shape."""
    if not max_chars or max_chars <= 0:
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return value
    return text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars; add a response projection to shrink this]"


_ENC = None
_ENC_TRIED = False


def _encoder():
    """A cached tiktoken encoder (cl100k_base), or None if tiktoken is unavailable."""
    global _ENC, _ENC_TRIED
    if not _ENC_TRIED:
        _ENC_TRIED = True
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - fall back to the char heuristic
            _ENC = None
    return _ENC


def count_tokens(obj: Any) -> int:
    """Accurate token count via tiktoken (cl100k_base) with a ~4-chars/token fallback.

    Used for the Raw-vs-Projected meter and any budget/guardrail that gates on size.
    """
    if obj is None:
        return 0
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    enc = _encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # noqa: BLE001
            pass
    return max(1, len(text) // 4)


# Back-compat alias (callers/tests may import estimate_tokens).
def estimate_tokens(obj: Any) -> int:
    return count_tokens(obj)
