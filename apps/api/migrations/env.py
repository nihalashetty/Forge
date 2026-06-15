"""Alembic environment for Forge.

Uses the app's `Base.metadata` as the autogenerate target and the configured
`FORGE_DATABASE_URL` (async drivers downgraded to their sync equivalent for the
migration connection). Future schema changes: `alembic revision --autogenerate -m '…'`.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

import forge.models  # noqa: F401 - register all tables on Base.metadata
from forge.config import settings
from forge.db.base import Base

config = context.config
target_metadata = Base.metadata


def _sync_url() -> str:
    url = settings.database_url
    return (
        url.replace("+aiosqlite", "")
        .replace("+asyncpg", "+psycopg2")
        .replace("postgresql+psycopg", "postgresql+psycopg2")
    )


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
