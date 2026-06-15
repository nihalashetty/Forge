"""Model pricing: USD per 1M tokens (input, output). Editable in admin later.

Unknown models price at 0 — but now log a warn-once per model so missing entries
surface in the server log instead of silently under-reporting SPEND. Keys are
matched by exact id, then by the bare model name (after the provider prefix).
Rates should be re-verified against provider price pages periodically.
"""

from __future__ import annotations

import logging

log = logging.getLogger("forge.pricing")

# (input_usd_per_1m, output_usd_per_1m)
PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-5.4": (1.25, 10.0),
    "gpt-5.4-mini": (0.3, 1.2),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    # Anthropic
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-sonnet-latest": (3.0, 15.0),
    "claude-3-5-haiku-latest": (0.8, 4.0),
    # Google
    "gemini-2.5-flash": (0.3, 2.5),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-1.5-flash": (0.075, 0.3),
}

_warned: set[str] = set()

# Admin overrides (loaded from the model_prices table at startup; edited via /v1/pricing).
# Consulted before the built-in defaults so rates can be fixed without a deploy. Kept as a
# plain dict so price() stays sync + fast for the tracer hot path.
_OVERRIDES: dict[str, tuple[float, float]] = {}


def set_override(model: str, input_per_1m: float, output_per_1m: float) -> None:
    _OVERRIDES[model] = (float(input_per_1m), float(output_per_1m))
    _warned.discard(model)


def load_overrides(overrides: dict[str, tuple[float, float]]) -> None:
    _OVERRIDES.clear()
    _OVERRIDES.update(overrides)


def merged_prices() -> dict[str, tuple[float, float]]:
    return {**PRICING, **_OVERRIDES}


def _bare(model: str) -> str:
    return model.split(":", 1)[1] if ":" in model else model


def price(model: str | None, input_tokens: int, output_tokens: int) -> float:
    if not model:
        return 0.0
    rate = (
        _OVERRIDES.get(model) or _OVERRIDES.get(_bare(model))
        or PRICING.get(model) or PRICING.get(_bare(model))
    )
    if not rate:
        if model not in _warned and not model.startswith("fake"):
            _warned.add(model)
            log.warning("No pricing entry for model %r — its runs report $0 cost. Add it to forge/tracing/pricing.py.", model)
        return 0.0
    in_rate, out_rate = rate
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
