"""Cost levers: accurate token counting + cheap-model defaults."""

from __future__ import annotations

from forge.engine.models import cheap_model_for_credentials, default_model_for_credentials
from forge.tools.projection import count_tokens, estimate_tokens


def test_count_tokens_nonzero_and_aliased():
    n = count_tokens("hello world, this is a token counting test")
    assert n > 0
    assert estimate_tokens("hello world, this is a token counting test") == n  # alias


def test_count_tokens_handles_objects_and_none():
    assert count_tokens(None) == 0
    assert count_tokens({"a": [1, 2, 3], "b": "text"}) > 0


def test_cheap_model_prefers_provider_nano_tier():
    assert cheap_model_for_credentials({"openai": "k"}) == "openai:gpt-4.1-nano"
    assert cheap_model_for_credentials({"anthropic": "k"}) == "anthropic:claude-haiku-4-5"
    assert cheap_model_for_credentials({}) is None
    # the cheap model is distinct from the default (frontier-ish) model
    assert cheap_model_for_credentials({"openai": "k"}) != default_model_for_credentials({"openai": "k"})
