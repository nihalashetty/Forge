"""Security + correctness hardening regression tests.

Covers high-risk behaviors: embed/run project scoping, anonymous thread-identity
isolation, atomic quota admission, the stale-run reaper, the resume-state guard,
JWT revocation, and component tenant/project scoping.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from forge.db.base import SessionLocal
from forge.models import Component, Run, Tenant, Workflow
from forge.services.components import ComponentService
from forge.services.runs import RunService


# --- S1: runs are scoped by project, not just tenant -------------------------------------

async def test_stream_is_scoped_by_project():
    async with SessionLocal() as s:
        run = Run(tenant_id="t1", project_id="pA", workflow_id="w", thread_id="th", status="queued", input={})
        s.add(run)
        await s.commit()
        rid = run.id
    rs = RunService(checkpointer=None)
    # Streaming the run under a DIFFERENT project (same tenant) must not find it.
    first = None
    async for frame in rs.stream(run_id=rid, tenant_id="t1", project_id="pB", public=True):
        first = frame
        break
    assert first["event"] == "error" and "not found" in first["data"]["message"]


async def test_resume_is_scoped_by_project():
    async with SessionLocal() as s:
        run = Run(tenant_id="t1", project_id="pA", workflow_id="w", thread_id="th", status="interrupted", input={})
        s.add(run)
        await s.commit()
        rid = run.id
    rs = RunService(checkpointer=None)
    res = await rs.resume(run_id=rid, tenant_id="t1", value=True, project_id="pB")
    assert res.get("error") == "run not found"


# --- S3: an anonymous caller can't attach to a thread bound to another identity ----------

async def test_create_run_isolates_foreign_identity_threads():
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t2", project_id="p", name="w", executable={}, status="active")
        s.add(wf)
        await s.commit()
        wid = wf.id
    rs = RunService()
    async with SessionLocal() as s:
        r1 = await rs.create_run(s, tenant_id="t2", project_id="p", workflow_id=wid, input={}, end_user={"id": "alice"})
        thread_alice = r1.thread_id
    # Anonymous caller supplies alice's thread_id -> must start a FRESH thread, not attach.
    async with SessionLocal() as s:
        r2 = await rs.create_run(s, tenant_id="t2", project_id="p", workflow_id=wid, input={}, thread_id=thread_alice, end_user=None)
        assert r2.thread_id != thread_alice
    # The same identity CAN continue its own thread.
    async with SessionLocal() as s:
        r3 = await rs.create_run(s, tenant_id="t2", project_id="p", workflow_id=wid, input={}, thread_id=thread_alice, end_user={"id": "alice"})
        assert r3.thread_id == thread_alice


# --- F2 / S2: quota admission is enforced (and atomic) -----------------------------------

async def test_run_admission_enforces_quota():
    from forge.services.quota import QuotaExceeded, run_admission

    async with SessionLocal() as s:
        t = Tenant(name="QA", settings={"max_runs_per_day": 1})
        s.add(t)
        await s.flush()
        s.add(Run(tenant_id=t.id, project_id="p", workflow_id="w", thread_id="th", status="done"))
        await s.commit()
        tid = t.id
    async with SessionLocal() as s:
        with pytest.raises(QuotaExceeded):
            async with run_admission(s, tid):
                pass


async def test_quota_ignores_errored_runs():
    from forge.services.quota import check_run_quota

    async with SessionLocal() as s:
        t = Tenant(name="QE", settings={"max_runs_per_day": 1})
        s.add(t)
        await s.flush()
        # An errored run must not consume the daily allowance.
        s.add(Run(tenant_id=t.id, project_id="p", workflow_id="w", thread_id="th", status="error"))
        await s.commit()
        await check_run_quota(s, t.id)  # must NOT raise


# --- F3: the reaper resolves stale queued/running runs -----------------------------------

async def test_reaper_marks_stale_runs():
    old = datetime.utcnow() - timedelta(hours=3)
    async with SessionLocal() as s:
        q = Run(tenant_id="t3", project_id="p", workflow_id="w", thread_id="th", status="queued", input={})
        r = Run(tenant_id="t3", project_id="p", workflow_id="w", thread_id="th", status="running", input={})
        s.add_all([q, r])
        await s.flush()
        q.created_at = old
        r.started_at = old
        await s.commit()
        qid, rid = q.id, r.id
    reaped = await RunService().reap_stale_runs(queued_max_age_s=60, running_max_age_s=60)
    assert reaped >= 2
    async with SessionLocal() as s:
        assert (await s.get(Run, qid)).status == "error"
        assert (await s.get(Run, rid)).status == "error"


# --- F-low: resume only an interrupted run -----------------------------------------------

async def test_resume_rejects_non_interrupted_run():
    async with SessionLocal() as s:
        run = Run(tenant_id="t4", project_id="p", workflow_id="w", thread_id="th", status="done", input={})
        s.add(run)
        await s.commit()
        rid = run.id
    res = await RunService(checkpointer=None).resume(run_id=rid, tenant_id="t4", value=True)
    assert res.get("error") and "not awaiting input" in res["error"]


# --- S11: identity (session) tokens can be revoked ---------------------------------------

def test_session_token_roundtrip_and_revocation():
    from forge.security import TokenError, create_session_token, decode_token, revoke

    tok = create_session_token(tenant_id="t", project_id="p", end_user={"id": "u"})
    claims = decode_token(tok, expected_type="session")
    assert claims["jti"] and claims["end_user"]["id"] == "u"
    revoke(claims["jti"])
    with pytest.raises(TokenError):
        decode_token(tok, expected_type="session")


# --- L2 / component isolation: get is scoped by project ----------------------------------

async def test_component_get_is_project_scoped():
    async with SessionLocal() as s:
        comp = await ComponentService.create(s, "tc", "projA", name="card", html="<div>{{x}}</div>")
        cid = comp.id
    async with SessionLocal() as s:
        assert await ComponentService.get(s, "tc", "projA", cid) is not None
        # Same tenant, wrong project -> not found (no cross-project read).
        assert await ComponentService.get(s, "tc", "projB", cid) is None


# --- S7: the SQL-tool DSN guard honors the per-project EgressPolicy instance --------------

async def test_sql_tool_honors_project_egress_policy():
    from forge.tools.sql import execute_sql
    from forge.util.ssrf import EgressBlocked, EgressPolicy

    cfg = {"name": "q", "query": "SELECT 1",
           "connection_url": "postgresql+asyncpg://u:p@blocked.example.com:5432/db"}
    # A resolved EgressPolicy INSTANCE (what ctx.egress_policy is) must be applied directly -
    # not silently rebuilt from global settings (which, in tests, block nothing).
    policy = EgressPolicy(block_private=False, deny_hosts=("blocked.example.com",))
    with pytest.raises(EgressBlocked):
        await execute_sql(cfg, {}, tenant_id="t", project_id="p", egress=policy)


# --- S4: a non-editor cannot self-assert privileged identity via the run body ------------

def test_role_gate_blocks_viewer_entitlements():
    from forge.services.auth import role_at_least

    # The create_run route strips roles/entitlements unless the caller is editor+.
    assert role_at_least("editor", "editor") is True
    assert role_at_least("viewer", "editor") is False
