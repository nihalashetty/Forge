"""Project-level default middleware (the Guardrails & Egress screen's `config.default_middleware`)
is prepended to EVERY agent's stack, ahead of the agent's own middleware. This locks the
"one enforcement point on every agent" guarantee the settings UI relies on."""

from __future__ import annotations

from langchain.agents.middleware import PIIMiddleware

from forge.engine.context import CompileContext
from forge.nodes.agent_node import _common_kwargs

# A PII guardrail entry exactly as the Guardrails settings screen compiles it.
_PII_DEFAULT = {
    "type": "pii",
    "config": {"_managed": True, "pii_type": "email", "strategy": "redact",
               "apply_to_input": True, "apply_to_output": True},
}


def _ctx(**kw) -> CompileContext:
    # `fake` model keeps resolve_model offline (no provider key / network).
    return CompileContext(tenant_id="t", project_id="p", default_model="fake", **kw)


def test_project_default_middleware_injected_when_agent_has_none():
    ctx = _ctx(project_default_mw=[_PII_DEFAULT])
    common = _common_kwargs({"model": "fake"}, ctx)
    assert any(isinstance(m, PIIMiddleware) for m in common["middleware"]), "project default PII guardrail must reach an agent with no middleware of its own"


def test_project_default_middleware_prepended_before_agent_middleware():
    ctx = _ctx(project_default_mw=[_PII_DEFAULT])
    config = {"model": "fake", "middleware": [{"type": "guardrail_regex", "config": {"patterns": ["secret"]}}]}
    built = _common_kwargs(config, ctx)["middleware"]
    names = [type(m).__name__ for m in built]
    # Default runs first (outermost); the agent's own middleware follows.
    assert names[0] == "PIIMiddleware"
    assert "_GuardrailRegexMiddleware" in names


def test_no_default_middleware_is_a_no_op():
    # Empty policy adds nothing — an agent with no middleware compiles to an empty stack
    # (so leaving the guardrails screen untouched has zero runtime effect / cost).
    built = _common_kwargs({"model": "fake"}, _ctx(project_default_mw=[]))["middleware"]
    assert built == []


def test_custom_pattern_pii_guardrail_compiles():
    # A "Custom pattern" row compiles to a `pii` entry with a regex `detector` and a custom
    # `pii_type` label — the shape the Guardrails screen emits for e.g. phone / national-ID.
    ctx = _ctx(project_default_mw=[{
        "type": "pii",
        "config": {"_managed": True, "pii_type": "phone", "detector": r"\d{3}-\d{3}-\d{4}", "strategy": "redact"},
    }])
    built = _common_kwargs({"model": "fake"}, ctx)["middleware"]
    m = next((x for x in built if isinstance(x, PIIMiddleware)), None)
    assert m is not None and m.pii_type == "phone"
