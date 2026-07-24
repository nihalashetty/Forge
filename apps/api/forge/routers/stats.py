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

from forge.db.base import engine
from forge.deps import current_tenant_id, get_session
from forge.models import Project, Span, Tool, Trace, Workflow

router = APIRouter(prefix="/v1/stats", tags=["stats"])

# Postgres (prod) and SQLite (dev/test) format dates differently. Bucket a timestamp to a
# "YYYY-MM-DD" string in SQL - grouped in the database, so a chart load never pulls a
# project's whole trace history into memory - via the right function for the active dialect.
_DIALECT = engine.dialect.name


def _day_bucket(col):
    if _DIALECT == "sqlite":
        return func.strftime("%Y-%m-%d", col)
    return func.to_char(func.date_trunc("day", col), "YYYY-MM-DD")


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


# --- analytics dashboard (time-series + breakdowns over a date range) ---------------------

class TimeBucketOut(BaseModel):
    date: str            # "YYYY-MM-DD" (one point per day across the whole range, gaps zero-filled)
    runs: int
    tokens: int
    cost_usd: float
    avg_latency_ms: int
    errors: int
    success: int


class SourceRollupOut(RollupOut):
    source: str


class ToolStatOut(BaseModel):
    name: str
    calls: int
    avg_latency_ms: int
    errors: int
    cost_usd: float
    tokens: int


class ModelStatOut(BaseModel):
    model: str
    calls: int
    tokens: int
    cost_usd: float
    avg_latency_ms: int


class LatencyBucketOut(BaseModel):
    label: str
    count: int


class AnalyticsRangeOut(BaseModel):
    days: int
    since: str
    until: str
    bucket: str


class AnalyticsOut(BaseModel):
    range: AnalyticsRangeOut
    totals: RollupOut               # windowed over the selected range
    prev_totals: RollupOut          # the immediately-preceding window of equal length (for deltas)
    timeseries: list[TimeBucketOut]
    by_source: list[SourceRollupOut]
    by_workflow: list[ReportRowOut]
    tools: list[ToolStatOut]
    models: list[ModelStatOut]
    latency_histogram: list[LatencyBucketOut]
    recent: list[RecentRunOut]

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


# Fixed latency buckets for the distribution histogram (upper bound in ms; None = open-ended).
_LATENCY_BUCKETS: list[tuple[str, int | None]] = [
    ("<250ms", 250), ("250-500ms", 500), ("500ms-1s", 1000), ("1-2s", 2000),
    ("2-5s", 5000), ("5-10s", 10000), (">10s", None),
]


def _ts_row(row) -> dict:
    """Fold one daily aggregate row into a time-series point (adds a success count)."""
    runs = int(row.runs or 0)
    return {
        "date": row.day,
        "runs": runs,
        "tokens": int(row.tokens or 0),
        "cost_usd": round(float(row.cost or 0.0), 6),
        "avg_latency_ms": int((row.latency_sum or 0) / runs) if runs else 0,
        "errors": int(row.errors or 0),
        "success": int(row.success or 0),
    }


@router.get("/projects/{project_id}/analytics", response_model=AnalyticsOut)
async def project_analytics(
    project_id: str,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    """Time-series + breakdowns for the project Analytics dashboard, over the last `days`.

    Everything is a grouped SQL aggregate (daily buckets, per-source, per-workflow, and
    per-tool/per-model spans) so a dashboard load returns a bounded number of rows no matter
    how large the trace history is. The previous equal-length window is rolled up too, so the
    UI can show period-over-period deltas on each KPI.
    """
    days = max(1, min(days, 365))
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)
    scope = (Trace.tenant_id == tenant_id, Trace.project_id == project_id)
    span_scope = (Trace.tenant_id == tenant_id, Trace.project_id == project_id, _ACTIVITY >= since)

    totals = _rollup((await session.execute(select(*_agg_columns()).where(*scope, _ACTIVITY >= since))).one())
    prev_totals = _rollup(
        (await session.execute(select(*_agg_columns()).where(*scope, _ACTIVITY >= prev_since, _ACTIVITY < since))).one()
    )

    # Daily time-series: one grouped row per calendar day present, then zero-fill the gaps so
    # the chart draws a continuous line across the whole range.
    day = _day_bucket(_ACTIVITY).label("day")
    ts_rows = (await session.execute(
        select(
            day, *_agg_columns(),
            func.coalesce(func.sum(case((Trace.status.in_(("done", "interrupted")), 1), else_=0)), 0).label("success"),
        ).where(*scope, _ACTIVITY >= since).group_by(day).order_by(day)
    )).all()
    by_day = {r.day: _ts_row(r) for r in ts_rows}
    timeseries: list[dict] = []
    cursor = since.date()
    end = now.date()
    while cursor <= end:
        key = cursor.isoformat()
        timeseries.append(by_day.get(key) or {
            "date": key, "runs": 0, "tokens": 0, "cost_usd": 0.0, "avg_latency_ms": 0, "errors": 0, "success": 0,
        })
        cursor += timedelta(days=1)

    # Per-source rollup (playground / api / embed / channels / assistant / ...).
    src_rows = (await session.execute(
        select(Trace.source, *_agg_columns()).where(*scope, _ACTIVITY >= since).group_by(Trace.source)
    )).all()
    by_source = [{"source": r.source or "-", **_rollup(r)} for r in src_rows]
    by_source.sort(key=lambda r: r["runs"], reverse=True)

    # Per-workflow (+ assistant + name-keyed "other") report rows, same shape as project_stats.
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
        select(kind, ident, *_agg_columns()).where(*scope, _ACTIVITY >= since).group_by(kind, ident)
    )).all()
    wf_names: dict[str, str] = {wid: name for wid, name in (await session.execute(
        select(Workflow.id, Workflow.name).where(Workflow.tenant_id == tenant_id, Workflow.project_id == project_id)
    )).all()}
    by_workflow = []
    for r in grouped:
        if r.kind == "assistant":
            label = "Forge Assistant"
        elif r.kind == "workflow":
            label = wf_names.get(r.ident, "(deleted workflow)")
        else:
            label = r.ident
        by_workflow.append({"label": label, "kind": r.kind, **_rollup(r)})
    by_workflow.sort(key=lambda r: r["cost_usd"], reverse=True)

    # Tool + model breakdowns from spans, joined to their trace for tenant/project/window scope.
    span_lat = func.coalesce(func.sum(Span.latency_ms), 0).label("latency_sum")
    span_calls = func.count().label("calls")
    span_tokens = func.coalesce(func.sum(Span.input_tokens + Span.output_tokens), 0).label("tokens")
    span_cost = func.coalesce(func.sum(Span.cost_usd), 0.0).label("cost")

    tool_rows = (await session.execute(
        select(
            Span.name, span_calls, span_lat, span_tokens, span_cost,
            func.coalesce(func.sum(case((Span.error.isnot(None), 1), else_=0)), 0).label("errors"),
        ).join(Trace, Trace.id == Span.trace_id)
        .where(*span_scope, Span.kind == "tool")
        .group_by(Span.name).order_by(span_calls.desc()).limit(12)
    )).all()
    tools = [
        {
            "name": r.name,
            "calls": int(r.calls or 0),
            "avg_latency_ms": int((r.latency_sum or 0) / r.calls) if r.calls else 0,
            "errors": int(r.errors or 0),
            "cost_usd": round(float(r.cost or 0.0), 6),
            "tokens": int(r.tokens or 0),
        }
        for r in tool_rows
    ]

    model_rows = (await session.execute(
        select(Span.model, span_calls, span_lat, span_tokens, span_cost)
        .join(Trace, Trace.id == Span.trace_id)
        .where(*span_scope, Span.kind == "llm", Span.model.isnot(None))
        .group_by(Span.model).order_by(span_cost.desc()).limit(12)
    )).all()
    models = [
        {
            "model": r.model,
            "calls": int(r.calls or 0),
            "tokens": int(r.tokens or 0),
            "cost_usd": round(float(r.cost or 0.0), 6),
            "avg_latency_ms": int((r.latency_sum or 0) / r.calls) if r.calls else 0,
        }
        for r in model_rows
    ]

    # Latency distribution: one row, a conditional count per bucket (SQL-side, no row scan).
    hist_cols = []
    lo = 0
    for i, (_, hi) in enumerate(_LATENCY_BUCKETS):
        if hi is None:
            cond = Trace.latency_ms >= lo
        elif lo == 0:
            cond = Trace.latency_ms < hi
        else:
            cond = (Trace.latency_ms >= lo) & (Trace.latency_ms < hi)
        hist_cols.append(func.coalesce(func.sum(case((cond, 1), else_=0)), 0).label(f"b{i}"))
        lo = hi or lo
    hist_row = (await session.execute(select(*hist_cols).where(*scope, _ACTIVITY >= since))).one()
    latency_histogram = [
        {"label": label, "count": int(getattr(hist_row, f"b{i}") or 0)}
        for i, (label, _) in enumerate(_LATENCY_BUCKETS)
    ]

    # 8 most recent runs in the window (for the activity feed).
    recent_rows = (await session.execute(
        select(
            Trace.id, Trace.workflow_id, Trace.name, Trace.status,
            Trace.total_tokens, Trace.latency_ms, Trace.total_cost_usd, Trace.started_at,
        ).where(*scope, _ACTIVITY >= since).order_by(Trace.started_at.desc()).limit(8)
    )).all()
    recent = [
        {
            "id": r.id,
            "workflow": wf_names.get(r.workflow_id or "", r.name or "run"),
            "project": "",
            "status": r.status,
            "tokens": r.total_tokens,
            "latency_ms": r.latency_ms,
            "cost_usd": round(r.total_cost_usd or 0.0, 6),
            "started_at": r.started_at.isoformat() if r.started_at else None,
        }
        for r in recent_rows
    ]

    return {
        "range": {"days": days, "since": since.isoformat(), "until": now.isoformat(), "bucket": "day"},
        "totals": totals,
        "prev_totals": prev_totals,
        "timeseries": timeseries,
        "by_source": by_source,
        "by_workflow": by_workflow,
        "tools": tools,
        "models": models,
        "latency_histogram": latency_histogram,
        "recent": recent,
    }
