"""QuotaService - per-tenant (and optionally per-project) daily usage caps.

Limits live in `tenant.settings` (max_runs_per_day / max_cost_per_day_usd /
max_tokens_per_day; 0 or unset = unlimited) and are checked at run creation against
today's accumulated usage from the `runs` table. Complements the per-minute rate limit
(util/ratelimit.py) with a spend ceiling.

Finding F6 hardening:
- PROJECTED-COST CEILING: finished-run cost alone lets a burst of concurrent runs (each
  still $0 until it finalizes) sail past a cost cap. We reserve a projected cost per
  in-flight run (settings.projected_run_cost_usd / max_cost_per_run_usd) so admission
  accounts for spend that is committed but not yet booked.
- PER-PROJECT SCOPING: `tenant.settings["project_limits"][project_id]` overrides the
  tenant-wide caps and scopes usage to that project (opt-in; absent => tenant-wide, as before).
- RESET TIMEZONE: the daily window resets at local midnight in settings.quota_reset_tz
  (tenant override `reset_tz`), not hard-coded UTC.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import case, func, select

from forge.config import settings
from forge.models import Run, Tenant
from forge.util.locks import tenant_run_locks

log = logging.getLogger("forge.quota")

# Run statuses that are admitted-and-still-consuming: their eventual cost isn't booked yet,
# so we reserve projected cost for them at admission time.
_INFLIGHT = ("queued", "running", "interrupted")


class QuotaExceeded(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@asynccontextmanager
async def run_admission(session, tenant_id: str, *, project_id: str | None = None):
    """Admit a run under the daily quota ATOMICALLY w.r.t. other admissions for the same
    tenant. The check-then-create race (N concurrent POSTs all reading the same pre-insert
    count) is closed by serializing admission per tenant and committing the new Run row
    inside the lock. Usage:

        async with run_admission(session, tenant_id):
            run = await run_service.create_run(...)   # inserts + commits the run row

    In-process only (single worker). Multi-worker needs a distributed lock / DB advisory
    lock; documented in util/locks.py."""
    lock = await tenant_run_locks.acquire_cm(tenant_id)
    async with lock:
        await check_run_quota(session, tenant_id, project_id=project_id)
        yield


async def _tenant_limits(session, tenant_id: str, project_id: str | None = None) -> dict:
    tenant = await session.get(Tenant, tenant_id)
    base = (tenant.settings or {}) if tenant else {}
    # Per-project overrides win when configured for THIS project (opt-in).
    if project_id:
        proj_over = (base.get("project_limits") or {}).get(project_id)
        if isinstance(proj_over, dict):
            return {**base, **proj_over, "_project_scoped": True}
    return base


def _reset_tz(limits: dict) -> str:
    return limits.get("reset_tz") or getattr(settings, "quota_reset_tz", None) or "UTC"


def _day_start_utc(tz_name: str) -> datetime:
    """Naive-UTC datetime of the most recent local midnight in `tz_name` (Run.created_at is
    naive UTC). Falls back to UTC midnight on any tz error."""
    now_utc = datetime.now(UTC)
    if tz_name and tz_name.upper() != "UTC":
        try:
            from zoneinfo import ZoneInfo
            local_midnight = now_utc.astimezone(ZoneInfo(tz_name)).replace(hour=0, minute=0, second=0, microsecond=0)
            return local_midnight.astimezone(UTC).replace(tzinfo=None)
        except Exception:  # noqa: BLE001 - bad tz name / no tzdata -> UTC
            log.warning("quota reset_tz %r invalid; using UTC", tz_name)
    return now_utc.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def _reserve_per_run(limits: dict) -> float:
    """USD reserved per in-flight run for the projected-cost ceiling."""
    return float(limits.get("projected_run_cost_usd") or limits.get("max_cost_per_run_usd")
                 or getattr(settings, "projected_run_cost_usd", 0.0) or 0.0)


async def _usage_row(session, tenant_id: str, day_start: datetime, project_id: str | None):
    where = [Run.tenant_id == tenant_id, Run.created_at >= day_start, Run.status != "error"]
    if project_id:
        where.append(Run.project_id == project_id)
    return (await session.execute(
        select(
            func.count(Run.id),
            func.coalesce(func.sum(Run.total_cost_usd), 0.0),
            func.coalesce(func.sum(Run.total_tokens), 0),
            func.coalesce(func.sum(case((Run.status.in_(_INFLIGHT), 1), else_=0)), 0),
        ).where(*where)
    )).one()


async def check_run_quota(session, tenant_id: str, *, project_id: str | None = None) -> None:
    """Raise QuotaExceeded if the tenant (optionally scoped to a project) has hit its daily
    run/cost/token cap, counting a projected reservation for in-flight runs against the cost cap."""
    limits = await _tenant_limits(session, tenant_id, project_id)
    max_runs = limits.get("max_runs_per_day")
    max_cost = limits.get("max_cost_per_day_usd")
    max_tokens = limits.get("max_tokens_per_day")
    if not (max_runs or max_cost or max_tokens):
        return

    # Scope usage to the project only when per-project limits are configured for it.
    scope_pid = project_id if limits.get("_project_scoped") else None
    day_start = _day_start_utc(_reset_tz(limits))
    # Count everything admitted today except runs that errored out (a failed run shouldn't
    # burn the daily allowance). Admitted-but-unfinished runs ARE counted, so a concurrent
    # burst is bounded once run_admission commits each row before the next check.
    row = await _usage_row(session, tenant_id, day_start, scope_pid)
    n_runs, cost, tokens, inflight = int(row[0]), float(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
    # Reserve projected cost for runs that are committed but not yet booked (denial-of-wallet
    # guard against a concurrent burst that is each still $0 at admission).
    projected_cost = cost + inflight * _reserve_per_run(limits)

    scope = " for this project" if scope_pid else ""
    if max_runs and n_runs >= int(max_runs):
        raise QuotaExceeded(f"daily run quota reached{scope} ({n_runs}/{max_runs})")
    if max_cost and projected_cost >= float(max_cost):
        raise QuotaExceeded(f"daily cost quota reached{scope} (${projected_cost:.2f}/${max_cost})")
    if max_tokens and tokens >= int(max_tokens):
        raise QuotaExceeded(f"daily token quota reached{scope} ({tokens}/{max_tokens})")


async def usage_today(session, tenant_id: str, *, project_id: str | None = None) -> dict:
    limits = await _tenant_limits(session, tenant_id, project_id)
    scope_pid = project_id if limits.get("_project_scoped") else None
    day_start = _day_start_utc(_reset_tz(limits))
    row = await _usage_row(session, tenant_id, day_start, scope_pid)
    inflight = int(row[3] or 0)
    return {"runs": int(row[0]), "cost_usd": round(float(row[1] or 0), 4), "tokens": int(row[2] or 0),
            "inflight": inflight, "reserved_cost_usd": round(inflight * _reserve_per_run(limits), 6),
            "reset_tz": _reset_tz(limits),
            "limits": {k: v for k, v in limits.items() if not k.startswith("_")}}
