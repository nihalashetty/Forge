"""Embedding spans (item 9): embed calls run through the Embedder are timed + priced as
`kind="embedding"` spans on the active run tracer, so RAG/memory embedding latency and cost
show up in traces. Wrapping at the Embedder level covers every call site (knowledge/*,
nodes/rag.py, services/memory.py) at once.
"""

from __future__ import annotations

from forge.knowledge.embeddings import _est_tokens, resolve_embedder
from forge.tracing.tracer import ForgeTracer, embedding_span


def test_est_tokens():
    assert _est_tokens(["aaaa"]) == 1  # 4 chars / 4
    assert _est_tokens(["", None]) == 0  # handles empties
    assert _est_tokens(["a" * 40, "b" * 40]) == 20


def test_embedding_span_noop_off_run():
    # No active tracer bound -> the context manager is a harmless no-op.
    with embedding_span("some:model", n_texts=3):
        pass


async def test_embed_call_records_embedding_span():
    tr = ForgeTracer()  # __init__ binds this as the active tracer for this async context
    embedder = resolve_embedder(None)  # default local fastembed
    await embedder.aembed_query("hello world")
    spans = [s for s in tr.ordered() if s.kind == "embedding"]
    assert spans, "an embed call should record an embedding span on the active tracer"
    assert spans[0].model == embedder.name
    assert spans[0].attributes.get("n_texts") == 1
    assert spans[0].end is not None and spans[0].end >= spans[0].start  # latency captured
