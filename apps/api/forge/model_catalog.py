"""Canonical chat-model catalog - the SINGLE source of truth for the model picker.

The frontend dropdown is served from here (`GET /v1/models`), and the built-in chat-model
rates in `forge.tracing.pricing` are derived from the same list. That means a model can only
appear in the UI if the backend also knows how to run and price it - the two can't drift, so
cost tracking never silently reports $0 for something a user actually selected.

Add a chat model = add one `ModelInfo` row here (with its price). Non-chat priced models
(embeddings) and any priced-but-not-offered models live in `pricing.py`'s extras.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str  # provider-prefixed id passed to init_chat_model, e.g. "openai:gpt-4o-mini"
    name: str  # short display name
    provider: str  # display label: OpenAI | Anthropic | Google | Local
    ctx: str  # context window (display only)
    tools: bool  # supports tool/function calling
    vision: bool  # accepts image input
    input_per_1m: float  # USD / 1M input tokens
    output_per_1m: float  # USD / 1M output tokens

    @property
    def bare(self) -> str:
        """Model id without the provider prefix - the key pricing matches on."""
        return self.id.split(":", 1)[1] if ":" in self.id else self.id


# Ordered cheap -> expensive within each provider so the fast/cheap picks surface first.
CHAT_MODELS: list[ModelInfo] = [
    # OpenAI
    ModelInfo("openai:gpt-4.1-nano", "gpt-4.1-nano", "OpenAI", "1M", True, True, 0.1, 0.4),
    ModelInfo("openai:gpt-4o-mini", "gpt-4o-mini", "OpenAI", "128k", True, True, 0.15, 0.6),
    ModelInfo("openai:gpt-4.1-mini", "gpt-4.1-mini", "OpenAI", "1M", True, True, 0.4, 1.6),
    ModelInfo("openai:gpt-4o", "gpt-4o", "OpenAI", "128k", True, True, 2.5, 10.0),
    # Anthropic
    ModelInfo("anthropic:claude-3-5-haiku-latest", "claude-3-5-haiku", "Anthropic", "200k", True, False, 0.8, 4.0),
    ModelInfo("anthropic:claude-haiku-4-5", "claude-haiku-4-5", "Anthropic", "200k", True, True, 1.0, 5.0),
    ModelInfo("anthropic:claude-3-5-sonnet-latest", "claude-3-5-sonnet", "Anthropic", "200k", True, True, 3.0, 15.0),
    ModelInfo("anthropic:claude-sonnet-4-6", "claude-sonnet-4-6", "Anthropic", "200k", True, True, 3.0, 15.0),
    # Google
    ModelInfo("google_genai:gemini-1.5-flash", "gemini-1.5-flash", "Google", "1M", True, True, 0.075, 0.3),
    ModelInfo("google_genai:gemini-2.5-flash", "gemini-2.5-flash", "Google", "1M", True, True, 0.3, 2.5),
    # Offline / test - runs with no provider credentials; never priced (see catalog_prices).
    ModelInfo("fake:echo", "fake (offline test)", "Local", "-", True, False, 0.0, 0.0),
]


def catalog_prices() -> dict[str, tuple[float, float]]:
    """Bare-name -> (input, output) rates for the catalog's real (non-fake) chat models.
    Merged into `pricing.PRICING` so the picker and the cost engine share one rate table."""
    return {
        m.bare: (m.input_per_1m, m.output_per_1m)
        for m in CHAT_MODELS
        if not m.id.startswith("fake")
    }


@dataclass(frozen=True)
class EmbeddingModel:
    id: str  # ref stored in rag_defaults.embedding_model, e.g. "fastembed:BAAI/bge-small-en-v1.5"
    name: str
    provider: str  # Local | OpenAI
    dim: int  # vector dimension (a Chroma collection is fixed-dim)
    billed: bool  # True => billed per token at ingest and on every query
    default: bool = False  # the embedder used when a project leaves it unset


@dataclass(frozen=True)
class RerankerModel:
    id: str  # cross-encoder id, e.g. "Xenova/ms-marco-MiniLM-L-6-v2"
    name: str
    note: str  # short size/quality hint for the picker
    default: bool = False


# Embedding models offered in the picker. `default` must match embeddings._DEFAULT_FASTEMBED;
# billed models must be priced in pricing.py (both guarded by test_model_catalog).
EMBEDDING_MODELS: list[EmbeddingModel] = [
    EmbeddingModel("fastembed:BAAI/bge-small-en-v1.5", "bge-small", "Local", 384, False, default=True),
    EmbeddingModel("fastembed:BAAI/bge-base-en-v1.5", "bge-base", "Local", 768, False),
    EmbeddingModel("openai:text-embedding-3-small", "OpenAI 3-small", "OpenAI", 1536, True),
    EmbeddingModel("openai:text-embedding-3-large", "OpenAI 3-large", "OpenAI", 3072, True),
]

# Cross-encoder rerankers (local, CPU, offline). `default` must match rerank.DEFAULT_RERANKER.
RERANKER_MODELS: list[RerankerModel] = [
    RerankerModel("Xenova/ms-marco-MiniLM-L-6-v2", "MiniLM-L6", "small, CPU-fast", default=True),
    RerankerModel("BAAI/bge-reranker-base", "bge-reranker-base", "heavier, more accurate"),
]
