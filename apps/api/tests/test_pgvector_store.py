"""Unit tests for the pgvector backend's selection + Chroma-where translation.

No live Postgres here: these cover the pure, bug-prone logic (Chroma where-dict -> SQL
predicate, vector literal encoding, sync-DSN derivation) and the make_store() backend switch.
The dense/hybrid search SQL shares its fusion + where semantics with the Chroma path that the
other knowledge tests already exercise end to end.
"""

from __future__ import annotations

import pytest

from forge.config import settings
from forge.knowledge import store as store_mod
from forge.knowledge.pgvector_store import (
    PgVectorStore,
    _sync_dsn,
    _to_vector_literal,
    _translate_where,
)
from forge.knowledge.store import make_store


def test_translate_where_and_eq():
    params: list = []
    sql = _translate_where({"$and": [{"tenant_id": {"$eq": "t1"}}, {"project_id": {"$eq": "p1"}}]}, params)
    assert "metadata->>%s" in sql and " AND " in sql
    # field names AND values are bound positionally, never string-formatted
    assert params == ["tenant_id", "t1", "project_id", "p1"]


def test_translate_where_in_and_shorthand():
    params: list = []
    sql = _translate_where({"kind": {"$in": ["faq", "hours"]}}, params)
    assert "= ANY(%s)" in sql
    assert params == ["kind", ["faq", "hours"]]
    # {field: value} shorthand behaves as $eq
    params2: list = []
    _translate_where({"source_id": "s1"}, params2)
    assert params2 == ["source_id", "s1"]


def test_translate_where_empty_is_true():
    assert _translate_where(None, []) == "TRUE"
    assert _translate_where({}, []) == "TRUE"


def test_translate_where_rejects_unknown_operator():
    # fail closed: an operator we don't model must never silently drop the filter
    with pytest.raises(ValueError):
        _translate_where({"score": {"$gt": 0.5}}, [])


def test_vector_literal_encoding():
    assert _to_vector_literal([0.0, 1.5, -2.0]) == "[0.0,1.5,-2.0]"


def test_sync_dsn_strips_async_driver(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@h:5432/db", raising=False)
    assert _sync_dsn() == "postgresql://u:p@h:5432/db"


def test_sync_dsn_rejects_sqlite(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///x.db", raising=False)
    with pytest.raises(RuntimeError):
        _sync_dsn()


def test_make_store_defaults_to_chroma(monkeypatch):
    # default backend selects Chroma; stub the ctor so no real on-disk client is built
    monkeypatch.setattr(settings, "vector_backend", "chroma", raising=False)
    marker = object()
    monkeypatch.setattr(store_mod, "ChromaStore", lambda collection="forge_kb": marker)
    assert make_store(collection="forge_kb_8") is marker


def test_make_store_selects_pgvector(monkeypatch):
    monkeypatch.setattr(settings, "vector_backend", "pgvector", raising=False)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@h/db", raising=False)
    monkeypatch.setattr(PgVectorStore, "_ensure_schema", lambda self: None)  # skip the DB bootstrap
    st = make_store(collection="forge_kb_16")
    assert isinstance(st, PgVectorStore)
    assert st._collection == "forge_kb_16"
    assert st._dsn == "postgresql://u:p@h/db"
