"""QuotaService — per-tenant daily usage caps.

Limits live in `tenant.settings` (max_runs_per_day / max_cost_per_day_usd /
max_tokens_per_day; 0 or unset = unlimited) and are checked at run creation against
today's accumulated usage from the `runs` table. Complements the per-minute rate limit
(util/ratelimit.py) with a spend ceiling.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from forge.models import Run, Tenant


class QuotaExceeded(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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
    row = (await session.execute(
        select(
            func.count(Run.id),
            func.coalesce(func.sum(Run.total_cost_usd), 0.0),
            func.coalesce(func.sum(Run.total_tokens), 0),
        ).where(Run.tenant_id == tenant_id, Run.created_at >= day_start)
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
