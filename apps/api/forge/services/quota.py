"""QuotaService - per-tenant daily usage caps.

Limits live in `tenant.settings` (max_runs_per_day / max_cost_per_day_usd /
max_tokens_per_day; 0 or unset = unlimited) and are checked at run creation against
today's accumulated usage from the `runs` table. Complements the per-minute rate limit
(util/ratelimit.py) with a spend ceiling.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import func, select

from forge.models import Run, Tenant
from forge.util.locks import tenant_run_locks


class QuotaExceeded(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@asynccontextmanager
async def run_admission(session, tenant_id: str):
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
        await check_run_quota(session, tenant_id)
        yield


async def _tenant_limits(session, tenant_id: str) -> dict:
    tenant = await session.get(Tenant, tenant_id)
    return (tenant.settings or {}) if tenant else {}


async def check_run_quota(session, tenant_id: str) -> None:
    """Raise QuotaExceeded if the tenant has hit its daily run/cost/token cap."""
    limits = await _tenant_limits(session, tenant_id)
    max_runs = limits.get("max_runs_per_day")
    max_cost = limits.get("max_cost_per_day_usd")
    max_tokens = limits.get("max_tokens_per_day")
    if not (max_runs or max_cost or max_tokens):
        return

    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    # Count everything admitted today except runs that errored out (a failed run shouldn't
    # burn the daily allowance). Admitted-but-unfinished runs ARE counted, so a concurrent
    # burst is bounded once run_admission commits each row before the next check.
    row = (await session.execute(
        select(
            func.count(Run.id),
            func.coalesce(func.sum(Run.total_cost_usd), 0.0),
            func.coalesce(func.sum(Run.total_tokens), 0),
        ).where(Run.tenant_id == tenant_id, Run.created_at >= day_start, Run.status != "error")
    )).one()
    n_runs, cost, tokens = int(row[0]), float(row[1] or 0), int(row[2] or 0)

    if max_runs and n_runs >= int(max_runs):
        raise QuotaExceeded(f"daily run quota reached ({n_runs}/{max_runs})")
    if max_cost and cost >= float(max_cost):
        raise QuotaExceeded(f"daily cost quota reached (${cost:.2f}/${max_cost})")
    if max_tokens and tokens >= int(max_tokens):
        raise QuotaExceeded(f"daily token quota reached ({tokens}/{max_tokens})")


async def usage_today(session, tenant_id: str) -> dict:
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    row = (await session.execute(
        select(
            func.count(Run.id),
            func.coalesce(func.sum(Run.total_cost_usd), 0.0),
            func.coalesce(func.sum(Run.total_tokens), 0),
        ).where(Run.tenant_id == tenant_id, Run.created_at >= day_start)
    )).one()
    return {"runs": int(row[0]), "cost_usd": round(float(row[1] or 0), 4), "tokens": int(row[2] or 0),
            "limits": await _tenant_limits(session, tenant_id)}
