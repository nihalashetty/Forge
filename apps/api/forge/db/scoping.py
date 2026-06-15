"""Tenant-scoping helpers — one place to enforce row-level tenant isolation.

Every query against a tenant-scoped table should go through `tenant_scoped` so the
`tenant_id` filter can never be forgotten. On Postgres this is backed up by Row-Level
Security (see `infra/postgres_rls.sql`), which is a DB-level guarantee even if a query
slips through; SQLite (dev) relies on this query-level scoping alone.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select

T = TypeVar("T")


def tenant_scoped(stmt: Select, model, tenant_id: str, *, project_id: str | None = None) -> Select:
    """Add `WHERE tenant_id = :tenant_id` (and optional project_id) to a select."""
    stmt = stmt.where(model.tenant_id == tenant_id)
    if project_id is not None and hasattr(model, "project_id"):
        stmt = stmt.where(model.project_id == project_id)
    return stmt
