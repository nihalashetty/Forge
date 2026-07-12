"""Dashboard stats - tenant-wide rollups over real traces (no placeholder numbers).

Rollups are computed as SQL aggregates (COUNT / SUM + GROUP BY) so a dashboard load never
pulls a tenant's entire trace history into memory - it returns a handful of grouped rows
regardless of how many traces exist. Derived fields (averages, rates) are computed in
Python from the raw sums/counts so the arithmetic stays identical to the previous
row-by-row implementation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.models import Project, Tool, Trace, Workflow

router = APIRouter(prefix="/v1/stats", tags=["stats"])


# --- response models (typed contract for the generated OpenAPI schema) --------------------

class RollupOut(BaseModel):
    runs: int
    tokens: int
    cost_usd: float
    avg_latency_ms: int
    errors: int
    error_rate: float


class RecentRunOut(BaseModel):
    id: str
    workflow: str
    project: str
    status: str
    tokens: int
    latency_ms: int
    cost_usd: float
    started_at: str | None = None


class ProjectCardStatsOut(BaseModel):
    workflows: int
    tools: int
    runs_7d: int


class DashboardReportOut(RollupOut):
    project_id: str
    project: str
    assistant_cost_usd: float
    assistant_turns: int


class DashboardStatsOut(BaseModel):
    runs_7d: int
    total_runs: int
    success_rate: float
    avg_latency_ms: int
    spend_7d: float
    recent: list[RecentRunOut]
    projects: dict[str, ProjectCardStatsOut]
    reports: list[DashboardReportOut]
    totals: RollupOut


class ReportRowOut(RollupOut):
    label: str
    kind: str


class AssistantRollupOut(RollupOut):
    turns: int


class ProjectStatsOut(BaseModel):
    totals: RollupOut
    last_7d: RollupOut
    assistant: AssistantRollupOut
    reports: list[ReportRowOut]

# When a trace has no start time, fall back to its insert time (matches the old
# `t.started_at or t.created_at`) for the 7-day activity window.
_ACTIVITY = func.coalesce(Trace.started_at, Trace.created_at)


def _agg_columns():
    """The five raw aggregates every rollup needs. Derived fields (avg, rate) come from these.
    Returned fresh each call so the same expressions can be reused across queries."""
    return (
        func.count().label("runs"),
        func.coalesce(func.sum(Trace.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(Trace.total_cost_usd), 0.0).label("cost"),
        func.coalesce(func.sum(Trace.latency_ms), 0).label("latency_sum"),
        func.coalesce(func.sum(case((Trace.status == "error", 1), else_=0)), 0).label("errors"),
    )


def _rollup(row) -> dict:
    """Fold one aggregate row (runs/tokens/cost/latency_sum/errors) into the rollup shape."""
    runs = int(row.runs or 0)
    errors = int(row.errors or 0)
    return {
        "runs": runs,
        "tokens": int(row.tokens or 0),
        "cost_usd": round(float(row.cost or 0.0), 6),
        "avg_latency_ms": int((row.latency_sum or 0) / runs) if runs else 0,
        "errors": errors,
        "error_rate": round(errors / runs * 100, 1) if runs else 0.0,
    }


@router.get("/dashboard", response_model=DashboardStatsOut)
async def dashboard(session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    since = datetime.utcnow() - timedelta(days=7)
    tenant = Trace.tenant_id == tenant_id

    # All-time totals (one row).
    totals = _rollup((await session.execute(select(*_agg_columns()).where(tenant))).one())

    # 7-day window aggregate (one row): count, success count, spend, latency sum.
    win = (await session.execute(
        select(
            func.count().label("runs"),
            func.coalesce(func.sum(case((Trace.status.in_(("done", "interrupted")), 1), else_=0)), 0).label("done"),
            func.coalesce(func.sum(Trace.total_cost_usd), 0.0).label("cost"),
            func.coalesce(func.sum(Trace.latency_ms), 0).label("latency_sum"),
        ).where(tenant, _ACTIVITY >= since)
    )).one()
    total = int(win.runs or 0)
    done = int(win.done or 0)
    avg_latency = int((win.latency_sum or 0) / total) if total else 0

    # Per-project counts for the dashboard cards (workflows, tools, 7-day runs).
    per_project: dict[str, dict] = {}

    def bucket(pid: str) -> dict:
        return per_project.setdefault(pid, {"workflows": 0, "tools": 0, "runs_7d": 0})

    for pid, n in (await session.execute(
        select(Workflow.project_id, func.count()).where(Workflow.tenant_id == tenant_id).group_by(Workflow.project_id)
    )).all():
        bucket(pid)["workflows"] = int(n)
    for pid, n in (await session.execute(
        select(Tool.project_id, func.count()).where(Tool.tenant_id == tenant_id).group_by(Tool.project_id)
    )).all():
        bucket(pid)["tools"] = int(n)
    for pid, n in (await session.execute(
        select(Trace.project_id, func.count()).where(tenant, _ACTIVITY >= since).group_by(Trace.project_id)
    )).all():
        bucket(pid)["runs_7d"] = int(n)

    # Name lookups (bounded by #workflows / #projects, not #traces).
    wf_names: dict[str, str] = {wid: name for wid, name in (await session.execute(
        select(Workflow.id, Workflow.name).where(Workflow.tenant_id == tenant_id)
    )).all()}
    proj_names: dict[str, str] = {pid: name for pid, name in (await session.execute(
        select(Project.id, Project.name).where(Project.tenant_id == tenant_id)
    )).all()}

    # 8 most recent all-time - only the columns the card renders.
    recent_rows = (await session.execute(
        select(
            Trace.id, Trace.workflow_id, Trace.project_id, Trace.status,
            Trace.total_tokens, Trace.latency_ms, Trace.total_cost_usd, Trace.started_at,
        ).where(tenant).order_by(Trace.started_at.desc()).limit(8)
    )).all()
    recent = [
        {
            "id": r.id,
            "workflow": wf_names.get(r.workflow_id or "", "run"),
            "project": proj_names.get(r.project_id, "-"),
            "status": r.status,
            "tokens": r.total_tokens,
            "latency_ms": r.latency_ms,
            "cost_usd": round(r.total_cost_usd or 0.0, 6),
            "started_at": r.started_at.isoformat() if r.started_at else None,
        }
        for r in recent_rows
    ]

    # Per-project report rows (all-time); assistant share via conditional aggregation.
    report_rows = (await session.execute(
        select(
            Trace.project_id,
            *_agg_columns(),
            func.coalesce(func.sum(case((Trace.name == "assistant", Trace.total_cost_usd), else_=0.0)), 0.0).label("asst_cost"),
            func.coalesce(func.sum(case((Trace.name == "assistant", 1), else_=0)), 0).label("asst_turns"),
        ).where(tenant).group_by(Trace.project_id)
    )).all()
    reports = [
        {
            "project_id": r.project_id,
            "project": proj_names.get(r.project_id, "(deleted project)"),
            **_rollup(r),
            "assistant_cost_usd": round(float(r.asst_cost or 0.0), 6),
            "assistant_turns": int(r.asst_turns or 0),
        }
        for r in report_rows
    ]
    reports.sort(key=lambda r: r["cost_usd"], reverse=True)

    return {
        "runs_7d": total,
        "total_runs": totals["runs"],
        "success_rate": round(done / total * 100, 1) if total else 0.0,
        "avg_latency_ms": avg_latency,
        "spend_7d": round(float(win.cost or 0.0), 6),
        "recent": recent,
        "projects": per_project,
        "reports": reports,
        "totals": totals,
    }


@router.get("/projects/{project_id}", response_model=ProjectStatsOut)
async def project_stats(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Project-scoped rollups + report rows (per workflow + Forge Assistant)."""
    since = datetime.utcnow() - timedelta(days=7)
    scope = (Trace.tenant_id == tenant_id, Trace.project_id == project_id)

    totals = _rollup((await session.execute(select(*_agg_columns()).where(*scope))).one())
    last_7d = _rollup((await session.execute(select(*_agg_columns()).where(*scope, _ACTIVITY >= since))).one())
    asst = (await session.execute(select(*_agg_columns()).where(*scope, Trace.name == "assistant"))).one()
    assistant = {**_rollup(asst), "turns": int(asst.runs or 0)}

    wf_names: dict[str, str] = {wid: name for wid, name in (await session.execute(
        select(Workflow.id, Workflow.name).where(Workflow.tenant_id == tenant_id, Workflow.project_id == project_id)
    )).all()}

    # Report rows grouped like the old _report_rows: one bucket for the assistant, one per
    # workflow_id, and an "other" bucket keyed by trace name for runs with no workflow.
    kind = case(
        (Trace.name == "assistant", "assistant"),
        (Trace.workflow_id.isnot(None), "workflow"),
        else_="other",
    ).label("kind")
    ident = case(
        (Trace.name == "assistant", "assistant"),
        (Trace.workflow_id.isnot(None), Trace.workflow_id),
        else_=Trace.name,
    ).label("ident")
    grouped = (await session.execute(
        select(kind, ident, *_agg_columns()).where(*scope).group_by(kind, ident)
    )).all()
    reports = []
    for r in grouped:
        if r.kind == "assistant":
            label = "Forge Assistant"
        elif r.kind == "workflow":
            label = wf_names.get(r.ident, "(deleted workflow)")
        else:
            label = r.ident
        reports.append({"label": label, "kind": r.kind, **_rollup(r)})
    reports.sort(key=lambda r: r["cost_usd"], reverse=True)

    return {
        "totals": totals,
        "last_7d": last_7d,
        "assistant": assistant,
        "reports": reports,
    }
