"""Postgres RLS tenant-GUC wiring.

Verifies the request-scoped tenant contextvar round-trips (used by the GUC listener in
forge.db.base to set app.current_tenant per transaction) and that the listener is a harmless
no-op on the SQLite test DB, so ordinary queries keep working. The GUC's effect on real RLS
policies is exercised only against Postgres (see infra/postgres_rls.sql)."""

from __future__ import annotations

from sqlalchemy import text

from forge.db.base import SessionLocal
from forge.db.scoping import current_tenant, set_current_tenant, tenant_guard


def test_tenant_contextvar_roundtrip():
    set_current_tenant(None)
    assert current_tenant() is None
    set_current_tenant("t-1")
    assert current_tenant() == "t-1"
    with tenant_guard("t-2"):
        assert current_tenant() == "t-2"
    assert current_tenant() == "t-1"
    set_current_tenant(None)


async def test_sqlite_session_unaffected_by_guc():
    with tenant_guard("any-tenant"):
        async with SessionLocal() as s:
            assert (await s.execute(text("SELECT 1"))).scalar() == 1
