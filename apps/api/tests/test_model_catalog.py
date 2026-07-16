"""Model-catalog integrity.

CHAT_MODELS is the single source of truth for the console's model picker, and the built-in
pricing rates derive from it. These tests enforce the invariant that prevents cost tracking
from silently reporting $0: every model a user can pick must be priced by the backend, and the
model a blank ("Project default") node actually runs must be selectable in the UI.
"""

from __future__ import annotations

from forge.engine.models import _PROVIDER_CHEAP_MODEL
from forge.model_catalog import CHAT_MODELS, EMBEDDING_MODELS, RERANKER_MODELS, catalog_prices
from forge.tracing.pricing import _resolve_rate, merged_prices


def test_every_offered_model_is_priced():
    # A model in the dropdown with no pricing entry would cost $0 at runtime (silent under-report).
    for m in CHAT_MODELS:
        if m.id.startswith("fake"):
            continue
        assert _resolve_rate(m.id) is not None, f"{m.id} is offered but has no pricing entry"


def test_cheap_defaults_are_selectable():
    # cheap_model_for_credentials picks these when a node leaves the model blank (e.g. the
    # classifier's "Project default"); each must be in the catalog so the UI can show what runs.
    ids = {m.id for m in CHAT_MODELS}
    for default in _PROVIDER_CHEAP_MODEL.values():
        assert default in ids, f"cheap default {default} is not in the model catalog"


def test_no_duplicate_model_ids():
    ids = [m.id for m in CHAT_MODELS]
    assert len(ids) == len(set(ids)), "duplicate model id in CHAT_MODELS"


def test_catalog_rates_are_the_ones_the_cost_engine_uses():
    # The picker's rates must be the SAME table the tracer prices with - not a divergent copy.
    prices = merged_prices()
    for bare, rate in catalog_prices().items():
        assert prices.get(bare) == rate, f"pricing for {bare} diverged from the catalog"


def test_embedding_default_matches_backend():
    # The picker's default embedder must be the one the backend actually falls back to.
    from forge.knowledge.embeddings import _DEFAULT_FASTEMBED

    defaults = [m for m in EMBEDDING_MODELS if m.default]
    assert len(defaults) == 1, "exactly one default embedding model"
    assert defaults[0].id.split(":", 1)[1] == _DEFAULT_FASTEMBED


def test_reranker_default_matches_backend():
    from forge.knowledge.rerank import DEFAULT_RERANKER

    defaults = [m for m in RERANKER_MODELS if m.default]
    assert len(defaults) == 1, "exactly one default reranker"
    assert defaults[0].id == DEFAULT_RERANKER


def test_billed_embeddings_are_priced():
    # A billed embedder with no pricing entry would embed at $0 (silent cost under-report).
    for m in EMBEDDING_MODELS:
        if m.billed:
            assert _resolve_rate(m.id) is not None, f"billed embedder {m.id} has no pricing entry"


def test_no_duplicate_ids_across_catalogs():
    all_ids = [m.id for m in CHAT_MODELS] + [m.id for m in EMBEDDING_MODELS] + [m.id for m in RERANKER_MODELS]
    assert len(all_ids) == len(set(all_ids)), "duplicate id across model catalogs"
