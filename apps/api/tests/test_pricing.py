"""Admin pricing overrides overlay the built-in defaults."""

from __future__ import annotations

from forge.tracing.pricing import load_overrides, merged_prices, price, set_override


def test_default_pricing_applies():
    # gpt-4o-mini default is (0.15, 0.6) per 1M
    cost = price("gpt-4o-mini", 1_000_000, 0)
    assert abs(cost - 0.15) < 1e-9


def test_override_takes_precedence_and_matches_bare_name():
    set_override("gpt-4o-mini", 1.0, 2.0)
    try:
        assert abs(price("gpt-4o-mini", 1_000_000, 0) - 1.0) < 1e-9
        assert abs(price("openai:gpt-4o-mini", 0, 1_000_000) - 2.0) < 1e-9  # provider-prefixed resolves bare
        assert merged_prices()["gpt-4o-mini"] == (1.0, 2.0)
    finally:
        load_overrides({})  # reset so other tests see defaults


def test_unknown_model_prices_zero():
    assert price("totally-unknown-model", 1000, 1000) == 0.0
