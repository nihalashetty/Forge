"""Resolve a model-ref string to a chat model.

Doc 2 §9 `resolve_model`: parse the provider-prefixed id; native packages for the
big three, gateways for the rest. A `fake:` scheme returns an offline model so the
engine, tests, and the playground "dry run" work with no API keys or network.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from forge.config import settings

if TYPE_CHECKING:
    from forge.engine.context import CompileContext


# Sensible per-provider default model, used when a project has a provider key
# configured but no explicit `default_model`. Keeps an agent node with no model
# from silently degrading to the offline `fake:` model. Ordered by preference.
_PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "openai": "openai:gpt-4.1-mini",
    "anthropic": "anthropic:claude-sonnet-4-6",
    "google_genai": "google_genai:gemini-2.5-flash",
    "google": "google_genai:gemini-2.5-flash",
}


def default_model_for_credentials(creds: dict | None) -> str | None:
    """Pick a real model ref for a project that has provider keys but no explicit
    default_model. Returns None when no known provider key is present (caller then
    falls back to the global offline default)."""
    for provider in ("openai", "anthropic", "google_genai", "google"):
        if provider in (creds or {}):
            return _PROVIDER_DEFAULT_MODEL[provider]
    return None


# Cheapest capable model per provider — for high-volume, low-stakes calls like intent
# classification, where a frontier model is wasted spend.
_PROVIDER_CHEAP_MODEL: dict[str, str] = {
    "openai": "openai:gpt-4.1-nano",
    "anthropic": "anthropic:claude-haiku-4-5",
    "google_genai": "google_genai:gemini-2.5-flash",
    "google": "google_genai:gemini-2.5-flash",
}


def cheap_model_for_credentials(creds: dict | None) -> str | None:
    """The cheapest model for a project's configured provider (classifier default)."""
    for provider in ("openai", "anthropic", "google_genai", "google"):
        if provider in (creds or {}):
            return _PROVIDER_CHEAP_MODEL[provider]
    return None


def make_fake_model(reply: str | None = None) -> BaseChatModel:
    """An offline chat model that returns a fixed final answer on every call.

    No tool calls => any ReAct loop terminates immediately. Uses a cycled iterator
    so repeated invocations never exhaust. `bind_tools` is a no-op so tool-bound
    agents still run offline (tools are bound but never invoked by this model).
    """
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    class _ToolTolerantFake(GenericFakeChatModel):
        def bind_tools(self, tools=None, **kwargs):  # type: ignore[override]
            return self

    text = reply or "Hello from Forge's offline model. Configure a real provider model to go live."
    return _ToolTolerantFake(messages=itertools.cycle([AIMessage(content=text)]))


def resolve_model(
    model_ref: str | BaseChatModel | None,
    ctx: CompileContext | None = None,
    params: dict[str, Any] | None = None,
) -> BaseChatModel:
    # Already a constructed model (e.g. injected by a test or middleware).
    if isinstance(model_ref, BaseChatModel):
        return model_ref

    if not model_ref:
        model_ref = (getattr(ctx, "default_model", None) if ctx else None) or settings.default_model

    if isinstance(model_ref, str) and model_ref.startswith("fake"):
        # "fake" or "fake:<text>"
        _, _, reply = model_ref.partition(":")
        return make_fake_model(reply or None)

    # Real provider / gateway. init_chat_model parses "provider:model" and binds the
    # right integration package; raises a clear ImportError if it isn't installed.
    from langchain.chat_models import init_chat_model

    clean = {k: v for k, v in (params or {}).items() if v is not None}

    # Inject the project's provider API key (resolved from a secret by the runtime
    # assembler into ctx.provider_credentials). Falls back to the provider's env var.
    provider = model_ref.split(":", 1)[0] if ":" in model_ref else None
    creds = getattr(ctx, "provider_credentials", None) or {}
    key = creds.get(provider) if provider else None
    if key:
        param = "google_api_key" if provider in ("google_genai", "google") else "api_key"
        clean.setdefault(param, key)

    return init_chat_model(model_ref, **clean)
