"""Platform-hardening tests (findings a-k): auth lifecycle, RBAC/API-keys, rate limiting,
audit pagination/export, project budgets, retention, OAuth PKCE, and ops guards.

In-process ASGI. Each test resets the shared in-process rate limiter + revocation state so the
process-wide singletons can't leak between tests.
"""

from __future__ import annotations

import json as _json
import time
import uuid

import httpx
import pytest

from forge.config import settings
from forge.main import create_app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")


def _email() -> str:
    return f"u{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(autouse=True)
def _reset_platform_state():
    from forge.security import _revocations
    from forge.util.ratelimit import rate_limiter

    try:
        rate_limiter._local._buckets.clear()
    except Exception:
        pass
    _revocations._jti.clear()
    _revocations._user_cut.clear()
    yield


# --- c: production hardening guard ------------------------------------------------------

def test_production_guard_flags_new_gaps():
    from forge.config import Settings

    s = Settings()
    s.environment = "production"
    s.jwt_secret = "x" * 40
    s.auth_required = True
    s.bootstrap_admin_password = "a-strong-password"
    s.egress_block_private = True
    s.database_url = "postgresql+asyncpg://u:p@db/forge"
    s.checkpoint_backend = "postgres"
    s.trusted_hosts = []                       # -> flagged
    s.public_base_url = "http://forge.example.com"   # http -> flagged
    s.public_console_url = "https://app.example.com"
    s.service_api_token = "tooshort"           # < min length -> flagged
    problems = s.validate_production()
    assert any("TRUSTED_HOSTS" in p for p in problems)
    assert any("PUBLIC_BASE_URL" in p for p in problems)
    assert any("SERVICE_API_TOKEN" in p for p in problems)

    # Fixing them clears exactly those problems.
    s.trusted_hosts = ["forge.example.com"]
    s.public_base_url = "https://forge.example.com"
    s.service_api_token = ""  # empty = disabled, allowed
    cleared = s.validate_production()
    assert not any(("TRUSTED_HOSTS" in p or "PUBLIC_BASE_URL" in p or "SERVICE_API_TOKEN" in p) for p in cleared)


def test_multi_worker_without_redis_warns():
    from forge.config import Settings

    s = Settings()
    s.web_concurrency = 4
    s.redis_url = None
    assert any("Multiple workers" in w for w in s.startup_warnings())


# --- b: rate limiter pruning + fail-closed public surface -------------------------------

def test_inprocess_bucket_prunes_idle_keys():
    import forge.util.ratelimit as rl

    limiter = rl.RateLimiter()
    limiter.allow("old", rate=100)
    limiter._buckets["old"].updated -= (rl._BUCKET_IDLE_TTL + 10)   # look idle
    limiter._last_prune -= (rl._BUCKET_PRUNE_EVERY + 10)            # allow a sweep
    limiter.allow("new", rate=100)                                  # triggers prune
    assert "old" not in limiter._buckets and "new" in limiter._buckets


def test_public_surface_fails_closed_when_redis_unavailable():
    from forge.util.ratelimit import ResilientRateLimiter, _RedisConn

    class _Down(_RedisConn):
        def __init__(self):
            super().__init__("redis://unreachable")

        def get(self):
            return None  # configured but never connects

    limiter = ResilientRateLimiter(_Down())
    assert limiter.allow("embed:key", rate=5) is False       # public -> DENY (fail closed)
    assert limiter.allow("runs:tenant", rate=5) is True      # non-public -> in-process fallback


def test_public_surface_fails_closed_on_redis_error():
    from forge.util.ratelimit import ResilientRateLimiter, _RedisConn

    class _Broken:
        def pipeline(self):
            raise RuntimeError("redis down")

    class _Conn(_RedisConn):
        def __init__(self):
            super().__init__("redis://x")
            self._client = _Broken()

        def get(self):
            return self._client

    limiter = ResilientRateLimiter(_Conn())
    assert limiter.allow("embed:abc", rate=5) is False


# --- f: project budgets + allowed-models ------------------------------------------------

async def test_project_budget_and_allowed_models():
    from forge.db.base import SessionLocal
    from forge.models import Project, Run
    from forge.services.budget import BudgetExceeded, ModelNotAllowed, enforce_project_budget

    async with SessionLocal() as s:
        p = Project(tenant_id="tb", name="B", slug="b", config={
            "allowed_models": ["openai:gpt-4o"],
            "budgets": {"monthly_usd_cap": 1.0, "max_usd_per_run": 0.0},
        })
        s.add(p)
        await s.commit()
        pid = p.id

    async with SessionLocal() as s:
        with pytest.raises(ModelNotAllowed):
            await enforce_project_budget(s, "tb", pid, model="anthropic:claude")
        await enforce_project_budget(s, "tb", pid, model="openai:gpt-4o")  # allowed, no spend yet

    async with SessionLocal() as s:
        s.add(Run(tenant_id="tb", project_id=pid, workflow_id="w", thread_id="t",
                  status="done", total_cost_usd=1.5))
        await s.commit()
    async with SessionLocal() as s:
        with pytest.raises(BudgetExceeded):
            await enforce_project_budget(s, "tb", pid, model="openai:gpt-4o")


def test_disallowed_workflow_models_at_publish():
    """Per-node allowed_models validation (item 6): every chat model in a workflow's nodes is
    checked at publish, mirroring the admission-time single-model check across all nodes."""
    from forge.services.budget import collect_workflow_models, disallowed_workflow_models

    executable = {
        "nodes": [
            {"id": "a", "type": "agent", "config": {"model": "openai:gpt-4o", "middleware": [
                {"kind": "model_fallback", "config": {"models": ["anthropic:claude", "openai:gpt-4o"]}},
            ]}},
            {"id": "l", "type": "llm", "config": {"model": "openai:gpt-4o"}},
            {"id": "r", "type": "retrieval", "config": {"embedding_model": "fastembed:bge"}},
            {"id": "e", "type": "end", "config": {}},
        ]
    }
    # agent/llm/classifier + nested middleware models are collected; the embedder is NOT.
    assert collect_workflow_models(executable) == {"openai:gpt-4o", "anthropic:claude"}
    # no allow-list => no-op (publish always allowed)
    assert disallowed_workflow_models({}, executable) == []
    # allow-list forbids the fallback's anthropic model
    assert disallowed_workflow_models({"allowed_models": ["openai:gpt-4o"]}, executable) == ["anthropic:claude"]
    # a fully-covered allow-list passes
    assert disallowed_workflow_models({"allowed_models": ["openai:gpt-4o", "anthropic:claude"]}, executable) == []


# --- a: auth endpoint throttling --------------------------------------------------------

async def test_login_is_throttled_per_email(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 3)
    async with _client() as c:
        email = _email()
        await c.post("/v1/auth/register", json={"email": email, "password": "supersecret1"})
        codes = [
            (await c.post("/v1/auth/login", json={"email": email, "password": "wrong"})).status_code
            for _ in range(6)
        ]
        assert 429 in codes


# --- d: refresh rotation, reuse detection, logout-all -----------------------------------

async def test_refresh_rotates_and_detects_reuse():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        old_rt = reg["refresh_token"]
        r1 = await c.post("/v1/auth/refresh", json={"refresh_token": old_rt})
        assert r1.status_code == 200
        new_rt = r1.json()["refresh_token"]
        assert new_rt != old_rt
        # Reusing the rotated (old) token is detected -> 401 and the whole family is revoked.
        assert (await c.post("/v1/auth/refresh", json={"refresh_token": old_rt})).status_code == 401
        assert (await c.post("/v1/auth/refresh", json={"refresh_token": new_rt})).status_code == 401


async def test_logout_all_invalidates_existing_access_token():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        assert (await c.get("/v1/auth/me", headers=h)).status_code == 200
        assert (await c.post("/v1/auth/logout-all", headers=h)).status_code == 200
        assert (await c.get("/v1/auth/me", headers=h)).status_code == 401


async def test_logout_revokes_refresh_token():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        rt = reg["refresh_token"]
        assert (await c.post("/v1/auth/logout", json={"refresh_token": rt})).status_code == 200
        assert (await c.post("/v1/auth/refresh", json={"refresh_token": rt})).status_code == 401


# --- h: API keys + per-project RBAC -----------------------------------------------------

async def test_api_key_lifecycle():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        created = await c.post("/v1/api-keys", json={"name": "ci", "role": "editor"}, headers=h)
        assert created.status_code == 201, created.text
        key, key_id = created.json()["key"], created.json()["id"]
        assert key.startswith("forge_sk_")

        kh = {"Authorization": f"Bearer {key}"}
        assert (await c.get("/v1/projects", headers=kh)).status_code == 200
        me = await c.get("/v1/auth/me", headers=kh)
        assert me.status_code == 200 and me.json()["role"] == "editor"

        assert (await c.delete(f"/v1/api-keys/{key_id}", headers=h)).status_code == 204
        assert (await c.get("/v1/projects", headers=kh)).status_code == 401


async def test_api_key_cannot_exceed_creator_role():
    async with _client() as c:
        # invite an editor, then that editor tries to mint an owner key
        owner = (await c.post("/v1/auth/register", json={"email": _email(), "password": "ownerpass1"})).json()
        oh = {"Authorization": f"Bearer {owner['access_token']}"}
        ed_email = _email()
        await c.post("/v1/team/members", json={"email": ed_email, "role": "admin", "password": "adminpass1"}, headers=oh)
        ed = (await c.post("/v1/auth/login", json={"email": ed_email, "password": "adminpass1"})).json()
        eh = {"Authorization": f"Bearer {ed['access_token']}"}
        assert (await c.post("/v1/api-keys", json={"name": "x", "role": "owner"}, headers=eh)).status_code == 403


async def test_per_project_membership_elevates_role():
    async with _client() as c:
        owner = (await c.post("/v1/auth/register", json={"email": _email(), "password": "ownerpass1"})).json()
        oh = {"Authorization": f"Bearer {owner['access_token']}"}
        member_email = _email()
        inv = await c.post("/v1/team/members",
                           json={"email": member_email, "role": "viewer", "password": "viewerpass1"}, headers=oh)
        member_id = inv.json()["id"]
        member = (await c.post("/v1/auth/login", json={"email": member_email, "password": "viewerpass1"})).json()
        mh = {"Authorization": f"Bearer {member['access_token']}"}

        pid = (await c.post("/v1/projects", json={"name": "P"}, headers=oh)).json()["id"]
        # Global viewer can't PATCH (admin-gated) the project...
        assert (await c.patch(f"/v1/projects/{pid}", json={"name": "X"}, headers=mh)).status_code == 403
        # ...until granted admin ON THIS PROJECT.
        assert (await c.put(f"/v1/projects/{pid}/members/{member_id}", json={"role": "admin"}, headers=oh)).status_code == 200
        assert (await c.patch(f"/v1/projects/{pid}", json={"name": "Y"}, headers=mh)).status_code == 200


# --- g: audit pagination, filters, export ----------------------------------------------

async def test_audit_pagination_filter_and_export():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        for i in range(3):
            await c.post("/v1/projects", json={"name": f"P{i}"}, headers=h)

        p1 = await c.get("/v1/audit?limit=2", headers=h)
        assert p1.status_code == 200 and len(p1.json()) == 2
        cursor = p1.headers.get("X-Next-Cursor")
        assert cursor
        p2 = await c.get(f"/v1/audit?limit=2&cursor={cursor}", headers=h)
        assert p2.status_code == 200 and len(p2.json()) >= 1
        assert {a["id"] for a in p1.json()}.isdisjoint({a["id"] for a in p2.json()})

        filtered = await c.get("/v1/audit", params={"action": "POST /v1/projects"}, headers=h)
        rows = filtered.json()
        assert len(rows) >= 3 and all(a["action"] == "POST /v1/projects" for a in rows)

        export = await c.get("/v1/audit/export", headers=h)
        assert export.status_code == 200
        lines = [ln for ln in export.text.splitlines() if ln.strip()]
        assert len(lines) >= 3 and all("action" in _json.loads(ln) for ln in lines)


# --- j: password reset, email verification, TOTP MFA -----------------------------------

async def test_password_reset_flow():
    async with _client() as c:
        email = _email()
        await c.post("/v1/auth/register", json={"email": email, "password": "origpass1"})
        rr = await c.post("/v1/auth/request-password-reset", json={"email": email})
        assert rr.status_code == 200
        url = rr.json().get("reset_url")   # no SMTP in tests -> link returned
        assert url and "reset=" in url
        token = url.split("reset=", 1)[1]
        assert (await c.post("/v1/auth/reset-password", json={"token": token, "password": "newpass123"})).status_code == 200
        assert (await c.post("/v1/auth/login", json={"email": email, "password": "origpass1"})).status_code == 401
        assert (await c.post("/v1/auth/login", json={"email": email, "password": "newpass123"})).status_code == 200


async def test_email_verification_flow():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        rv = await c.post("/v1/auth/request-email-verification", headers=h)
        assert rv.status_code == 200
        url = rv.json().get("verify_url")
        assert url and "verify_email=" in url
        token = url.split("verify_email=", 1)[1]
        assert (await c.post("/v1/auth/verify-email", json={"token": token})).status_code == 200


async def test_totp_enroll_confirm_and_enforced_at_login():
    from forge.security import _totp_at

    async with _client() as c:
        email = _email()
        reg = (await c.post("/v1/auth/register", json={"email": email, "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        secret = (await c.post("/v1/auth/mfa/totp/enroll", headers=h)).json()["secret"]
        code = _totp_at(secret, int(time.time() // 30))
        cf = await c.post("/v1/auth/mfa/totp/confirm", json={"code": code}, headers=h)
        assert cf.status_code == 200 and cf.json()["mfa_enabled"] is True
        # login now requires a valid code
        assert (await c.post("/v1/auth/login", json={"email": email, "password": "supersecret1"})).status_code == 401
        ok = await c.post("/v1/auth/login", json={
            "email": email, "password": "supersecret1", "totp_code": _totp_at(secret, int(time.time() // 30))})
        assert ok.status_code == 200


# --- k: workspace admin + readiness -----------------------------------------------------

async def test_workspace_get_and_update():
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        assert (await c.get("/v1/workspace", headers=h)).status_code == 200
        u = await c.patch("/v1/workspace", json={"name": "Renamed WS", "settings": {"max_runs_per_day": 5}}, headers=h)
        assert u.status_code == 200
        body = u.json()
        assert body["name"] == "Renamed WS" and body["settings"]["max_runs_per_day"] == 5


async def test_readyz_reports_dependency_checks():
    async with _client() as c:
        body = (await c.get("/readyz")).json()
        assert "checks" in body
        assert "db" in body["checks"] and "checkpointer" in body["checks"] and "vector_store" in body["checks"]


async def test_global_rate_limit_middleware(monkeypatch):
    monkeypatch.setattr(settings, "api_rate_limit_per_minute", 3)
    async with _client() as c:
        reg = (await c.post("/v1/auth/register", json={"email": _email(), "password": "supersecret1"})).json()
        h = {"Authorization": f"Bearer {reg['access_token']}"}
        from forge.util.ratelimit import rate_limiter
        rate_limiter._local._buckets.clear()  # isolate the GET burst from the register POST
        codes = [(await c.get("/v1/auth/me", headers=h)).status_code for _ in range(6)]
        assert codes.count(200) == 3 and 429 in codes  # burst of 3 (rate), then throttled


# --- e: scheduled retention purge -------------------------------------------------------

async def test_retention_purges_past_horizon():
    from datetime import datetime, timedelta

    from forge.db.base import SessionLocal
    from forge.models import Project, Run, Span, Trace
    from forge.services.retention import RetentionService

    async with SessionLocal() as s:
        p = Project(tenant_id="tret", name="R", slug="r", config={"tracing": {"retention_days": 7}})
        s.add(p)
        await s.flush()
        pid = p.id
        old = datetime.utcnow() - timedelta(days=30)
        tr = Trace(tenant_id="tret", project_id=pid, run_id="r1", name="t", status="done")
        old_run = Run(tenant_id="tret", project_id=pid, workflow_id="w", thread_id="th", status="done")
        recent_run = Run(tenant_id="tret", project_id=pid, workflow_id="w", thread_id="th2", status="done")
        s.add_all([tr, old_run, recent_run])
        await s.flush()
        sp = Span(tenant_id="tret", trace_id=tr.id, name="s", kind="node")
        s.add(sp)
        await s.flush()
        tr.created_at = old
        old_run.created_at = old
        await s.commit()
        trace_id, span_id, old_run_id, recent_run_id = tr.id, sp.id, old_run.id, recent_run.id

    counts = await RetentionService.purge_expired()
    assert counts["traces"] >= 1 and counts["runs"] >= 1 and counts["spans"] >= 1

    async with SessionLocal() as s:
        assert await s.get(Trace, trace_id) is None       # aged out
        assert await s.get(Span, span_id) is None          # its span too
        assert await s.get(Run, old_run_id) is None
        assert await s.get(Run, recent_run_id) is not None  # within horizon -> kept
