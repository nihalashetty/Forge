"""Per-tenant daily quota, centralized mutation auditing, and the scoping helper."""

from __future__ import annotations

import uuid

import httpx
import pytest

from forge.db.base import SessionLocal
from forge.db.scoping import tenant_scoped
from forge.main import create_app
from forge.models import Project, Run, Tenant
from forge.services.quota import QuotaExceeded, check_run_quota, usage_today


def _email() -> str:
    return f"u{uuid.uuid4().hex[:10]}@example.com"


# --- 1.7 quota ---


async def test_quota_blocks_when_daily_run_cap_reached():
    async with SessionLocal() as s:
        t = Tenant(name="Q", settings={"max_runs_per_day": 1})
        s.add(t)
        await s.flush()
        s.add(Run(tenant_id=t.id, project_id="p", workflow_id="w", thread_id="th", status="done"))
        await s.commit()
        tid = t.id
    async with SessionLocal() as s:
        with pytest.raises(QuotaExceeded):
            await check_run_quota(s, tid)
        usage = await usage_today(s, tid)
        assert usage["runs"] == 1 and usage["limits"]["max_runs_per_day"] == 1


async def test_no_quota_when_unset():
    async with SessionLocal() as s:
        t = Tenant(name="NoQ", settings={})
        s.add(t)
        await s.commit()
        await check_run_quota(s, t.id)  # must not raise


# --- 1.10 scoping helper ---


def test_tenant_scoped_adds_filters():
    from sqlalchemy import select

    from forge.models import Workflow
    sql = str(tenant_scoped(select(Workflow), Workflow, "t1", project_id="p1"))
    assert "tenant_id" in sql and "project_id" in sql


# --- 1.8 audit middleware ---


async def test_mutations_are_audited():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}

        r = await c.post("/v1/projects", json={"name": "Audited Project"}, headers=h)
        assert r.status_code in (200, 201), r.text

        audit = (await c.get("/v1/audit", headers=h)).json()
        actions = [a["action"] for a in audit]
        assert any(a == "POST /v1/projects" for a in actions), actions
        # auth endpoints are NOT double-audited by the middleware
        assert "POST /v1/auth/register" not in actions
