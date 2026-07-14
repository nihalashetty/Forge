"""pgvector-backed EmbeddingStore - the production alternative to the embedded ChromaStore.

Selected by `settings.vector_backend == "pgvector"` via `store.make_store()`. Unlike Chroma
(an on-disk index a single process owns), every worker connects to the same Postgres, so
vectors are shared across a horizontally-scaled deployment.

Design
------
- One table (`kb_vectors`) holds every collection's rows; the `collection` column carries the
  dim-keyed name (e.g. forge_kb_384) the caller already uses, so different embedders never mix
  in a distance comparison (each query is scoped to one collection => one dim).
- The `embedding` column is an unmodified `vector` (no fixed dimension) so collections of
  different dims coexist in the table. Searches are exact (a scoped sequential scan ordered by
  cosine distance) - correct and simple; an ANN index (HNSW) is a per-dim follow-up.
- Chunk metadata is stored as JSONB and the Chroma-style where-dicts the callers build
  ($and / $eq / $in over metadata fields) are translated to SQL predicates over it, so the
  store is a drop-in for ChromaStore without changing any call site.

Connections are opened per operation (psycopg v3, synchronous - store methods are already
called off the event loop in a threadpool), which is the thread-safe choice and needs no
connection-pool dependency. Requires Postgres with the `vector` extension available.

Interface parity with ChromaStore: upsert / query / query_where / hybrid_query / dump /
delete_ids / delete_by_source / delete_where / count / count_where / list_docs / get_texts /
ids_where.
"""

from __future__ import annotations

import json
import threading

from forge.config import settings
from forge.knowledge.store import _CORPUS_CAP, Hit, _bump_version, _hybrid_fuse, _where

_TABLE = "kb_vectors"
# DSNs whose schema (extension + table + indexes) has been ensured this process.
_INITIALIZED: set[str] = set()
_INIT_LOCK = threading.Lock()


def _sync_dsn() -> str:
    """A libpq-compatible DSN for psycopg from the app's async database_url (drop the
    SQLAlchemy driver suffix). pgvector requires Postgres; a sqlite url is a misconfiguration."""
    url = settings.database_url
    if url.startswith("sqlite"):
        raise RuntimeError(
            "vector_backend='pgvector' requires a Postgres database_url; got a sqlite url."
        )
    return url.replace("+asyncpg", "").replace("+psycopg", "")


def _to_vector_literal(embedding) -> str:
    # pgvector accepts the text form '[v1,v2,...]'; we cast it with ::vector in SQL.
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _translate_where(where: dict | None, params: list) -> str:
    """Translate the Chroma where-dict subset Forge builds ($and/$or of {field: {$eq|$in}},
    plus the {field: value} shorthand) into a SQL predicate over the JSONB `metadata` column.
    Field names AND values are bound as parameters (never string-formatted), so this is
    injection-safe even though the fields are internal. Unsupported operators fail closed."""
    if not where:
        return "TRUE"
    if "$and" in where:
        return "(" + " AND ".join(_translate_where(c, params) for c in where["$and"]) + ")"
    if "$or" in where:
        return "(" + " OR ".join(_translate_where(c, params) for c in where["$or"]) + ")"
    clauses: list[str] = []
    for field, cond in where.items():
        cond = cond if isinstance(cond, dict) else {"$eq": cond}
        for op, val in cond.items():
            if op == "$eq":
                params.extend([field, str(val)])
                clauses.append("(metadata->>%s) = %s")
            elif op == "$in":
                params.extend([field, [str(v) for v in (val or [])]])
                clauses.append("(metadata->>%s) = ANY(%s)")
            else:
                raise ValueError(f"pgvector store: unsupported where operator {op!r}")
    return "(" + " AND ".join(clauses) + ")" if len(clauses) > 1 else (clauses[0] if clauses else "TRUE")


class PgVectorStore:
    def __init__(self, collection: str = "forge_kb") -> None:
        self._collection = collection
        self._dsn = _sync_dsn()
        # `_key` namespaces the shared BM25 cache (store._hybrid_fuse) per DSN + collection.
        self._key = (self._dsn, collection)
        self._ensure_schema()

    # --- connection / schema ------------------------------------------------------------
    def _connect(self):
        import psycopg

        return psycopg.connect(self._dsn)

    def _ensure_schema(self) -> None:
        if self._dsn in _INITIALIZED:
            return
        with _INIT_LOCK:
            if self._dsn in _INITIALIZED:
                return
            with self._connect() as conn, conn.cursor() as cur:
                # The extension may already be installed by an admin without CREATE privilege
                # for the app role; ignore a permission failure and rely on the type existing.
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    conn.commit()
                except Exception:  # noqa: BLE001 - extension pre-provisioned / no privilege
                    conn.rollback()
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {_TABLE} (
                        collection  text        NOT NULL,
                        id          text        NOT NULL,
                        document    text,
                        metadata    jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding   vector      NOT NULL,
                        PRIMARY KEY (collection, id)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_{_TABLE}_metadata ON {_TABLE} USING gin (metadata)"
                )
                conn.commit()
            _INITIALIZED.add(self._dsn)

    def _scope(self, where: dict | None, params: list) -> str:
        """Collection scope + the translated metadata predicate (collection first so a scan
        only ever compares vectors of one dimension)."""
        params.append(self._collection)
        return "collection = %s AND " + _translate_where(where, params)

    # --- writes -------------------------------------------------------------------------
    def upsert(self, *, ids, embeddings, documents, metadatas) -> None:
        if not ids:
            return
        rows = [
            (self._collection, _id, documents[i], json.dumps(metadatas[i] or {}),
             _to_vector_literal(embeddings[i]))
            for i, _id in enumerate(ids)
        ]
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                f"""
                INSERT INTO {_TABLE} (collection, id, document, metadata, embedding)
                VALUES (%s, %s, %s, %s::jsonb, %s::vector)
                ON CONFLICT (collection, id) DO UPDATE SET
                    document = EXCLUDED.document,
                    metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding
                """,
                rows,
            )
            conn.commit()
        _bump_version(self._key)

    def delete_ids(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {_TABLE} WHERE collection = %s AND id = ANY(%s)",
                [self._collection, list(ids)],
            )
            conn.commit()
        _bump_version(self._key)

    def delete_by_source(self, source_id: str, *, tenant_id: str | None = None, project_id: str | None = None) -> None:
        clauses: list[dict] = [{"source_id": {"$eq": source_id}}]
        if tenant_id:
            clauses.append({"tenant_id": {"$eq": tenant_id}})
        if project_id:
            clauses.append({"project_id": {"$eq": project_id}})
        self.delete_where({"$and": clauses} if len(clauses) > 1 else clauses[0])

    def delete_where(self, where: dict) -> None:
        params: list = []
        scope = self._scope(where, params)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_TABLE} WHERE {scope}", params)
            conn.commit()
        _bump_version(self._key)

    # --- reads --------------------------------------------------------------------------
    def query(self, *, embedding, tenant_id, project_id, top_k=5, source_ids=None) -> list[Hit]:
        return self.query_where(
            embedding=embedding, where=_where(tenant_id, project_id, source_ids), top_k=top_k
        )

    def query_where(self, *, embedding, where: dict, top_k: int = 5) -> list[Hit]:
        qvec = _to_vector_literal(embedding)
        params: list = [qvec]  # SELECT score term
        scope = self._scope(where, params)
        params.extend([qvec, top_k])  # ORDER BY term + LIMIT
        sql = (
            f"SELECT id, document, metadata, 1 - (embedding <=> %s::vector) AS score "
            f"FROM {_TABLE} WHERE {scope} ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [
                Hit(id=r[0], text=r[1] or "", score=float(r[2]), metadata=r[3] or {})
                for r in cur.fetchall()
            ]

    def _get_documents(self, where: dict, limit: int | None = None) -> list[Hit]:
        """All stored chunks matching `where` (no vector query) - the corpus a lexical index
        is built over. score is 0.0 (unranked); `limit` caps the scan."""
        params: list = []
        scope = self._scope(where, params)
        sql = f"SELECT id, document, metadata FROM {_TABLE} WHERE {scope}"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [Hit(id=r[0], text=r[1] or "", score=0.0, metadata=r[2] or {}) for r in cur.fetchall()]

    def hybrid_query(
        self, *, embedding, query: str, tenant_id, project_id, top_k=5, source_ids=None,
        candidate_pool: int | None = None, corpus_cap: int = _CORPUS_CAP,
    ) -> list[Hit]:
        """Dense+BM25 RRF fusion, identical semantics to ChromaStore (see store._hybrid_fuse)."""
        return _hybrid_fuse(
            self, embedding=embedding, query=query, where=_where(tenant_id, project_id, source_ids),
            top_k=top_k, candidate_pool=candidate_pool, corpus_cap=corpus_cap,
        )

    def dump(self, where: dict, limit: int | None = None, *, ids: list[str] | None = None) -> dict:
        """Raw rows INCLUDING embedding vectors (parsed back to float lists) - the input to the
        chunk map's dimensionality reduction. Matches ChromaStore.dump's shape."""
        if ids:
            params: list = [self._collection, list(ids)]
            sql = f"SELECT id, document, metadata, embedding::text FROM {_TABLE} WHERE collection = %s AND id = ANY(%s)"
        else:
            params = []
            scope = self._scope(where, params)
            sql = f"SELECT id, document, metadata, embedding::text FROM {_TABLE} WHERE {scope}"
            if limit:
                sql += " LIMIT %s"
                params.append(limit)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                out = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
                for r in cur.fetchall():
                    out["ids"].append(r[0])
                    out["documents"].append(r[1] or "")
                    out["metadatas"].append(r[2] or {})
                    out["embeddings"].append(json.loads(r[3]) if r[3] else [])
                return out
        except Exception:  # noqa: BLE001 - table empty / not ready
            return {"ids": [], "documents": [], "metadatas": [], "embeddings": []}

    def count(self, tenant_id: str, project_id: str) -> int:
        return self.count_where(_where(tenant_id, project_id, None))

    def count_where(self, where: dict) -> int:
        params: list = []
        scope = self._scope(where, params)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {_TABLE} WHERE {scope}", params)
                return int(cur.fetchone()[0])
        except Exception:  # noqa: BLE001
            return 0

    def list_docs(self, where: dict) -> dict:
        """ids + documents + metadatas (NO embeddings) for `where`."""
        rows = self._get_documents(where)
        return {
            "ids": [h.id for h in rows],
            "documents": [h.text for h in rows],
            "metadatas": [h.metadata for h in rows],
        }

    def get_texts(self, ids: list[str], where: dict) -> dict:
        """Documents + metadatas for specific `ids`, ADDITIONALLY constrained by `where` - so a
        caller-supplied id can't read a row outside its tenant/project."""
        if not ids:
            return {"ids": [], "documents": [], "metadatas": []}
        params: list = []
        scope = self._scope(where, params)
        params.append(list(ids))
        sql = f"SELECT id, document, metadata FROM {_TABLE} WHERE {scope} AND id = ANY(%s)"
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception:  # noqa: BLE001
            return {"ids": [], "documents": [], "metadatas": []}
        return {
            "ids": [r[0] for r in rows],
            "documents": [r[1] or "" for r in rows],
            "metadatas": [r[2] or {} for r in rows],
        }

    def ids_where(self, where: dict) -> list[str]:
        params: list = []
        scope = self._scope(where, params)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT id FROM {_TABLE} WHERE {scope}", params)
                return [r[0] for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            return []
