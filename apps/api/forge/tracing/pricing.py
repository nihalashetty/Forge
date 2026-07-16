"""Model pricing: USD per 1M tokens (input, output). Editable in admin later.

Unknown models price at 0 - but now log a warn-once per model so missing entries
surface in the server log instead of silently under-reporting SPEND. Keys are
matched by exact id, then by the bare model name (after the provider prefix).
Rates should be re-verified against provider price pages periodically.
"""

from __future__ import annotations

import logging
import re

from forge.model_catalog import catalog_prices

log = logging.getLogger("forge.pricing")

# Cache-tier multipliers on the INPUT rate (provider-typical, Anthropic-style): a cache READ
# bills ~0.1x input, a cache WRITE ~1.25x. Applied when usage carries prompt-caching token
# details so cost is right precisely when the platform's own caching middleware is active.
_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 1.25

# (input_usd_per_1m, output_usd_per_1m)
# Priced but NOT offered in the picker: embeddings, plus frontier / less-common chat models
# kept here for accurate cost tracking if a workflow references one directly. The models that
# ARE offered come from forge.model_catalog (single source of truth) and are merged below - so
# every selectable model is always priced and the two can't drift.
_EXTRA_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4.1": (2.0, 8.0),
    "gpt-5.4": (1.25, 10.0),
    "gpt-5.4-mini": (0.3, 1.2),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    # Anthropic
    "claude-opus-4-8": (5.0, 25.0),
    # Google
    "gemini-2.5-pro": (1.25, 10.0),
}

PRICING: dict[str, tuple[float, float]] = {**catalog_prices(), **_EXTRA_PRICING}

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


# Trailing snapshot/alias suffixes that don't change the price: a date stamp
# (gpt-4o-2024-11-20, claude-3-5-sonnet-20241022) or an alias (-latest / -preview).
_SNAPSHOT_SUFFIX = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8}|latest|preview)$")


def _resolve_rate(model: str) -> tuple[float, float] | None:
    """Resolve a (input, output) rate, tolerating date-stamped / unlisted model ids so a
    snapshot like `openai:gpt-4o-2024-11-20` doesn't silently price at $0. Order: exact id,
    bare name, date/alias-stripped name, then the longest known key that is a prefix of the
    bare name (covers new dated variants of a listed family)."""
    merged = {**PRICING, **_OVERRIDES}
    bare = _bare(model)
    for key in (model, bare):
        if key in merged:
            return merged[key]
    stripped = _SNAPSHOT_SUFFIX.sub("", bare)
    if stripped != bare and stripped in merged:
        return merged[stripped]
    best: str | None = None
    for key in merged:
        if bare.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return merged[best] if best else None


def price(
    model: str | None, input_tokens: int, output_tokens: int,
    *, cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
) -> float:
    if not model:
        return 0.0
    rate = _resolve_rate(model)
    if not rate:
        if model not in _warned and not model.startswith("fake"):
            _warned.add(model)
            log.warning("No pricing entry for model %r - its runs report $0 cost. Add it to forge/tracing/pricing.py.", model)
        return 0.0
    in_rate, out_rate = rate
    # `input_tokens` (from usage_metadata) is the TOTAL prompt tokens and already includes any
    # cache read/write tokens, so subtract them before pricing at the full input rate, then
    # bill each cache tier at its multiplier - otherwise cached reads (cheap) are over-charged.
    cached = min(cache_read_tokens + cache_creation_tokens, input_tokens)
    base_input = max(0, input_tokens - cached)
    cost = (base_input / 1_000_000) * in_rate
    cost += (cache_read_tokens / 1_000_000) * in_rate * _CACHE_READ_MULT
    cost += (cache_creation_tokens / 1_000_000) * in_rate * _CACHE_WRITE_MULT
    cost += (output_tokens / 1_000_000) * out_rate
    return cost
