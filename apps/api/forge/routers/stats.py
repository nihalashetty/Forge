"""Dashboard stats — tenant-wide rollups over real traces (no placeholder numbers)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.models import Project, Tool, Trace, Workflow

router = APIRouter(prefix="/v1/stats", tags=["stats"])


def _rollup(traces) -> dict:
    runs = len(traces)
    tokens = sum(t.total_tokens or 0 for t in traces)
    cost = sum(t.total_cost_usd or 0.0 for t in traces)
    latency = int(sum(t.latency_ms or 0 for t in traces) / runs) if runs else 0
    errors = sum(1 for t in traces if getattr(t, "status", None) == "error")
    return {
        "runs": runs, "tokens": tokens, "cost_usd": round(cost, 6), "avg_latency_ms": latency,
        "errors": errors, "error_rate": round(errors / runs * 100, 1) if runs else 0.0,
    }


def _report_rows(traces, wf_names: dict[str, str]) -> list[dict]:
    """Group traces into report rows: one per workflow (its playground/API runs) plus
    one for the Forge Assistant (its turns are traced with name='assistant')."""
    groups: dict[tuple, list] = {}
    for t in traces:
        if t.name == "assistant":
            key = ("assistant", "assistant")
        elif t.workflow_id:
            key = ("workflow", t.workflow_id)
        else:
            key = ("other", t.name)
        groups.setdefault(key, []).append(t)
    rows = []
    for (kind, ident), ts in groups.items():
        label = "Forge Assistant" if kind == "assistant" else wf_names.get(ident, ident if kind == "other" else "(deleted workflow)")
        rows.append({"label": label, "kind": kind, **_rollup(ts)})
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)
    return rows


@router.get("/dashboard")
async def dashboard(session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    rows = (
        await session.execute(
            select(Trace).where(Trace.tenant_id == tenant_id).order_by(Trace.started_at.desc())
        )
    ).scalars().all()

    since = datetime.utcnow() - timedelta(days=7)
    window = [t for t in rows if (t.started_at or t.created_at) and (t.started_at or t.created_at) >= since]
    total = len(window)
    done = sum(1 for t in window if t.status in ("done", "interrupted"))
    spend = sum(t.total_cost_usd or 0.0 for t in window)
    avg_latency = int(sum(t.latency_ms or 0 for t in window) / total) if total else 0

    workflows = (await session.execute(select(Workflow).where(Workflow.tenant_id == tenant_id))).scalars().all()
    tools = (await session.execute(select(Tool).where(Tool.tenant_id == tenant_id))).scalars().all()
    wf_names = {w.id: w.name for w in workflows}
    proj_names = {p.id: p.name for p in (await session.execute(select(Project).where(Project.tenant_id == tenant_id))).scalars()}

    # Real per-project rollups for the dashboard project cards.
    per_project: dict[str, dict] = {}
    for w in workflows:
        per_project.setdefault(w.project_id, {"workflows": 0, "tools": 0, "runs_7d": 0})["workflows"] += 1
    for t in tools:
        per_project.setdefault(t.project_id, {"workflows": 0, "tools": 0, "runs_7d": 0})["tools"] += 1
    for t in window:
        per_project.setdefault(t.project_id, {"workflows": 0, "tools": 0, "runs_7d": 0})["runs_7d"] += 1

    recent = [
        {
            "id": t.id,
            "workflow": wf_names.get(t.workflow_id or "", "run"),
            "project": proj_names.get(t.project_id, "—"),
            "status": t.status,
            "tokens": t.total_tokens,
            "latency_ms": t.latency_ms,
            "cost_usd": round(t.total_cost_usd or 0.0, 6),
            "started_at": t.started_at.isoformat() if t.started_at else None,
        }
        for t in rows[:8]
    ]

    # Per-project report rows (all-time): cost / calls / latency, assistant included.
    by_project: dict[str, list] = {}
    for t in rows:
        by_project.setdefault(t.project_id, []).append(t)
    reports = []
    for pid, ts in by_project.items():
        assistant = [t for t in ts if t.name == "assistant"]
        reports.append({
            "project_id": pid,
            "project": proj_names.get(pid, "(deleted project)"),
            **_rollup(ts),
            "assistant_cost_usd": round(sum(t.total_cost_usd or 0.0 for t in assistant), 6),
            "assistant_turns": len(assistant),
        })
    reports.sort(key=lambda r: r["cost_usd"], reverse=True)

    return {
        "runs_7d": total,
        "total_runs": len(rows),
        "success_rate": round(done / total * 100, 1) if total else 0.0,
        "avg_latency_ms": avg_latency,
        "spend_7d": round(spend, 6),
        "recent": recent,
        "projects": per_project,
        "reports": reports,
        "totals": _rollup(rows),
    }


@router.get("/projects/{project_id}")
async def project_stats(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    """Project-scoped rollups + report rows (per workflow + Forge Assistant)."""
    rows = (
        await session.execute(
            select(Trace).where(Trace.tenant_id == tenant_id, Trace.project_id == project_id)
            .order_by(Trace.started_at.desc())
        )
    ).scalars().all()

    since = datetime.utcnow() - timedelta(days=7)
    window = [t for t in rows if (t.started_at or t.created_at) and (t.started_at or t.created_at) >= since]
    wf_names = {
        w.id: w.name
        for w in (await session.execute(select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.project_id == project_id))).scalars()
    }
    assistant = [t for t in rows if t.name == "assistant"]
    return {
        "totals": _rollup(rows),
        "last_7d": _rollup(window),
        "assistant": {**_rollup(assistant), "turns": len(assistant)},
        "reports": _report_rows(rows, wf_names),
    }
