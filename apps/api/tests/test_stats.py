"""Characterization tests for the stats rollups (dashboard + project_stats).

These pin the EXACT output of the two endpoints for a controlled dataset so the
in-memory -> SQL-aggregate refactor can be proven behaviour-preserving. Every expected
number here is hand-computed from the seeded traces below.

Dataset (tenant t_stats), 6 traces across 2 projects:
  A  P1 W1  run        done   tok 10  cost 0.10  lat 100   -1h   (in window)
  B  P1 W1  run        error  tok 20  cost 0.20  lat 300   -2h   (in window)
  C  P1 --  assistant  done   tok  5  cost 0.05  lat  50   -3h   (in window)
  D  P2 --  adhoc      done   tok  8  cost 0.08  lat  80   -4h   (in window)
  E  P2 wf_gone run     done  tok  2  cost 0.02  lat  20   -5h   (in window)
  F  P1 W1  run        done   tok100  cost 1.00  lat1000  -10d   (OUT of window)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from forge.db.base import SessionLocal
from forge.models import Project, Tool, Trace, Workflow
from forge.routers.stats import dashboard, project_stats

TENANT = "t_stats"


async def _seed() -> tuple[str, str]:
    now = datetime.utcnow()

    def h(n):  # n hours ago
        return now - timedelta(hours=n)

    async with SessionLocal() as s:
        p1 = Project(tenant_id=TENANT, name="Alpha", slug="alpha")
        p2 = Project(tenant_id=TENANT, name="Beta", slug="beta")
        s.add_all([p1, p2])
        await s.flush()
        w1 = Workflow(tenant_id=TENANT, project_id=p1.id, name="Support")
        s.add(w1)
        s.add(Tool(tenant_id=TENANT, project_id=p1.id, name="t", kind="builtin", config={}))
        await s.flush()
        rows = [
            Trace(tenant_id=TENANT, project_id=p1.id, workflow_id=w1.id, run_id="rA", name="run", status="done", total_tokens=10, total_cost_usd=0.10, latency_ms=100, started_at=h(1)),
            Trace(tenant_id=TENANT, project_id=p1.id, workflow_id=w1.id, run_id="rB", name="run", status="error", total_tokens=20, total_cost_usd=0.20, latency_ms=300, started_at=h(2)),
            Trace(tenant_id=TENANT, project_id=p1.id, workflow_id=None, run_id="rC", name="assistant", status="done", total_tokens=5, total_cost_usd=0.05, latency_ms=50, started_at=h(3)),
            Trace(tenant_id=TENANT, project_id=p2.id, workflow_id=None, run_id="rD", name="adhoc", status="done", total_tokens=8, total_cost_usd=0.08, latency_ms=80, started_at=h(4)),
            Trace(tenant_id=TENANT, project_id=p2.id, workflow_id="wf_gone", run_id="rE", name="run", status="done", total_tokens=2, total_cost_usd=0.02, latency_ms=20, started_at=h(5)),
            Trace(tenant_id=TENANT, project_id=p1.id, workflow_id=w1.id, run_id="rF", name="run", status="done", total_tokens=100, total_cost_usd=1.00, latency_ms=1000, started_at=now - timedelta(days=10)),
        ]
        s.add_all(rows)
        await s.commit()
        return p1.id, p2.id


async def test_dashboard_rollups():
    p1, p2 = await _seed()
    async with SessionLocal() as s:
        d = await dashboard(session=s, tenant_id=TENANT)

    assert d["total_runs"] == 6
    assert d["runs_7d"] == 5
    assert d["success_rate"] == 80.0          # 4 done of 5 in-window
    assert d["avg_latency_ms"] == 110         # int((100+300+50+80+20)/5)
    assert d["spend_7d"] == 0.45

    # per-project counts for the dashboard cards
    assert d["projects"][p1] == {"workflows": 1, "tools": 1, "runs_7d": 3}
    assert d["projects"][p2] == {"workflows": 0, "tools": 0, "runs_7d": 2}

    # recent = 8 most recent all-time, newest first (A,B,C,D,E,F)
    recent = [(r["workflow"], r["project"], r["status"], r["tokens"]) for r in d["recent"]]
    assert recent == [
        ("Support", "Alpha", "done", 10),
        ("Support", "Alpha", "error", 20),
        ("run", "Alpha", "done", 5),
        ("run", "Beta", "done", 8),
        ("run", "Beta", "done", 2),
        ("Support", "Alpha", "done", 100),
    ]

    # totals (all-time)
    assert d["totals"] == {
        "runs": 6, "tokens": 145, "cost_usd": 1.45, "avg_latency_ms": 258,
        "errors": 1, "error_rate": 16.7,
    }

    # reports (per project, all-time), cost desc
    reps = {r["project_id"]: r for r in d["reports"]}
    assert [r["project_id"] for r in d["reports"]] == [p1, p2]
    assert reps[p1]["project"] == "Alpha" and reps[p1]["runs"] == 4 and reps[p1]["tokens"] == 135
    assert reps[p1]["cost_usd"] == 1.35 and reps[p1]["avg_latency_ms"] == 362
    assert reps[p1]["errors"] == 1 and reps[p1]["error_rate"] == 25.0
    assert reps[p1]["assistant_cost_usd"] == 0.05 and reps[p1]["assistant_turns"] == 1
    assert reps[p2]["runs"] == 2 and reps[p2]["cost_usd"] == 0.1 and reps[p2]["assistant_turns"] == 0


async def test_project_stats_rollups():
    p1, p2 = await _seed()
    async with SessionLocal() as s:
        d1 = await project_stats(project_id=p1, session=s, tenant_id=TENANT)
        d2 = await project_stats(project_id=p2, session=s, tenant_id=TENANT)

    assert d1["totals"] == {"runs": 4, "tokens": 135, "cost_usd": 1.35, "avg_latency_ms": 362, "errors": 1, "error_rate": 25.0}
    assert d1["last_7d"] == {"runs": 3, "tokens": 35, "cost_usd": 0.35, "avg_latency_ms": 150, "errors": 1, "error_rate": 33.3}
    assert d1["assistant"] == {"runs": 1, "tokens": 5, "cost_usd": 0.05, "avg_latency_ms": 50, "errors": 0, "error_rate": 0.0, "turns": 1}

    # report rows for P1: workflow "Support" (A,B,F) then assistant (C), cost desc
    r1 = d1["reports"]
    assert [(r["kind"], r["label"], r["runs"], r["cost_usd"]) for r in r1] == [
        ("workflow", "Support", 3, 1.3),
        ("assistant", "Forge Assistant", 1, 0.05),
    ]

    # P2 exercises the 'other' (name) group and the deleted-workflow label
    r2 = d2["reports"]
    assert [(r["kind"], r["label"], r["runs"], r["cost_usd"]) for r in r2] == [
        ("other", "adhoc", 1, 0.08),
        ("workflow", "(deleted workflow)", 1, 0.02),
    ]
