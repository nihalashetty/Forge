"""Optional arq/Redis job queue for OFFLOADED run execution (audit P1).

The interactive SSE path stays inline (it must stream tokens to the caller). The
*non-interactive* paths — webhook + schedule triggers — don't need a synchronous reply,
so when a Redis/arq worker is configured they're enqueued instead of run on the web
process, which keeps long LLM runs off the request thread and gives backpressure +
retries via the worker tier. With no Redis configured, `enqueue_run` returns False and
the caller runs inline (the always-available default).

Safe to import anywhere: arq is imported lazily inside the functions, so the API process
doesn't need the `workers` extra installed.
"""

from __future__ import annotations

import contextlib
import logging

from forge.config import settings

log = logging.getLogger("forge.queue")

_pool = None


def queue_enabled() -> bool:
    if not settings.redis_url:
        return False
    try:
        import arq  # noqa: F401
    except Exception:  # noqa: BLE001 - arq (workers extra) not installed
        return False
    return True


async def _get_pool():
    global _pool
    if _pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue_run(run_id: str, tenant_id: str, project_id: str | None = None) -> bool:
    """Enqueue a run for the worker. Returns False (caller runs inline) when no queue is
    configured or enqueue fails — so a Redis blip degrades to inline, never drops the run."""
    if not queue_enabled():
        return False
    try:
        pool = await _get_pool()
        await pool.enqueue_job("run_job", run_id, tenant_id, project_id)
        return True
    except Exception:  # noqa: BLE001
        log.exception("failed to enqueue run %s; falling back to inline execution", run_id)
        return False


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        with contextlib.suppress(Exception):
            await _pool.aclose()
        _pool = None
