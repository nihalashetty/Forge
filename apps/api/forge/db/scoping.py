"""Tenant-scoping helpers - one place to enforce row-level tenant isolation.

Every query against a tenant-scoped table should go through `tenant_scoped` so the
`tenant_id` filter can never be forgotten. On Postgres this is backed up by Row-Level
Security (see `infra/postgres_rls.sql`), which is a DB-level guarantee even if a query
slips through; SQLite (dev) relies on this query-level scoping alone.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar

from sqlalchemy import Select

T = TypeVar("T")


def tenant_scoped(stmt: Select, model, tenant_id: str, *, project_id: str | None = None) -> Select:
    """Add `WHERE tenant_id = :tenant_id` (and optional project_id) to a select."""
    stmt = stmt.where(model.tenant_id == tenant_id)
    if project_id is not None and hasattr(model, "project_id"):
        stmt = stmt.where(model.project_id == project_id)
    return stmt


# Request-scoped tenant id, read by the Postgres RLS GUC listener (see forge.db.base) so the
# `app.current_tenant` setting is populated on every transaction and infra/postgres_rls.sql
# policies actually filter rows. Set by the `current_tenant_id` dependency for authenticated
# routes and by `tenant_guard(...)` around non-request work (runs, dispatch, scheduler). It is
# defense-in-depth ON TOP OF the explicit `WHERE tenant_id=` scoping - never the only guard.
_current_tenant: ContextVar[str | None] = ContextVar("forge_current_tenant", default=None)


def set_current_tenant(tenant_id: str | None):
    return _current_tenant.set(tenant_id)


def current_tenant() -> str | None:
    return _current_tenant.get()


@contextmanager
def tenant_guard(tenant_id: str | None):
    """Bind the current tenant for the duration of a block (non-request code paths: run
    execution, trigger dispatch, scheduler). Resets on exit so it can't leak across tasks."""
    token = _current_tenant.set(tenant_id)
    try:
        yield
    finally:
        _current_tenant.reset(token)
