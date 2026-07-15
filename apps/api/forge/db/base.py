"""Async engine, session factory, and declarative base.

SQLite (aiosqlite) by default; set FORGE_DATABASE_URL to a Postgres async URL in
prod (no code change). `init_db` creates tables for dev; Alembic owns prod migrations.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from forge.config import settings


class Base(DeclarativeBase):
    pass


def new_uuid() -> str:
    return str(uuid.uuid4())


class PkTimestamp:
    """Mixin: string-UUID primary key + created/updated timestamps."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


# SQLite needs check_same_thread off for the async driver's connection sharing.
_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
# SQLite (dev/test, via aiosqlite): pooling connections across asyncio event loops - e.g. the
# per-test loops pytest-asyncio spins up - leaves a connection to be torn down in a loop other
# than the one that opened it, causing intermittent "Task was destroyed but it is pending" /
# "object NoneType can't be used in 'await'" teardown errors. NullPool opens a fresh connection
# per checkout (cheap for a local sqlite file) and closes it immediately, so nothing lingers
# across loops. Postgres (prod) keeps the default pooled behaviour - no perf change there.
_engine_kwargs = {"poolclass": NullPool} if _is_sqlite else {}
engine = create_async_engine(
    settings.database_url, echo=False, future=True, connect_args=_connect_args, **_engine_kwargs
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# --- Postgres Row-Level Security wiring --------------------------------------------------
# infra/postgres_rls.sql policies filter on current_setting('app.current_tenant'). Nothing set
# that GUC before, so applying the (FORCE) RLS policies returned ZERO rows and broke the app.
# Set it per-transaction from the request-scoped tenant contextvar (forge.db.scoping) via
# set_config(..., is_local=true) so it auto-resets at commit/rollback. Postgres-only: SQLite
# (dev/test) has no RLS, so this listener isn't attached there and the suite is unaffected.
# Defensive: a failure to set the GUC must never break the transaction.
if not _is_sqlite:
    from sqlalchemy import event, text

    @event.listens_for(engine.sync_engine, "begin")
    def _apply_tenant_guc(conn):  # pragma: no cover - only exercised against Postgres
        try:
            from forge.db.scoping import current_tenant

            tid = current_tenant()
            if tid:
                conn.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tid)})
        except Exception:  # noqa: BLE001 - RLS GUC is best-effort; never break the txn
            pass


async def init_db() -> None:
    # Import models so they register on Base.metadata before create_all.
    from forge import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_new_columns)


def _ensure_new_columns(conn) -> None:
    """Dev-grade additive migration: create_all never alters existing tables, so
    columns added to the ORM after a table exists must be ALTERed in. Alembic owns
    real migrations in prod; this covers the SQLite dev database."""
    from sqlalchemy import inspect, text

    wanted = {
        "kb_sources": {"folder": "VARCHAR(200) NOT NULL DEFAULT ''"},
        "triggers": {"metadata": "JSON DEFAULT '{}'"},
        "agents": {"created_by": "VARCHAR(36)", "created_by_email": "VARCHAR(320)"},
        "mcp_clients": {"disabled_tools": "JSON DEFAULT '[]'"},
        "api_keys": {"user_id": "VARCHAR(36)", "project_id": "VARCHAR(36)"},
        "tool_sets": {"exposed": "BOOLEAN NOT NULL DEFAULT 1"},
        "projects": {"embed_key": "VARCHAR(64)"},
        "spans": {"input": "JSON", "output": "JSON"},
        "runs": {"source": "VARCHAR(40) NOT NULL DEFAULT 'playground'"},
        "traces": {
            "source": "VARCHAR(40) NOT NULL DEFAULT 'playground'",
            "actor": "VARCHAR(300) NOT NULL DEFAULT 'System'",
            "end_user_id": "VARCHAR(200)",
            "user_message": "TEXT",
            "ai_response": "TEXT",
        },
    }
    inspector = inspect(conn)
    for table, columns in wanted.items():
        if table not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for col, ddl in columns.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
