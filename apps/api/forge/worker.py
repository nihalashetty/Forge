"""arq worker entrypoint — offloaded run execution (audit P1).

Run it (needs the `workers` + `postgres` extras and FORGE_REDIS_URL):

    arq forge.worker.WorkerSettings

It builds the SAME durable checkpointer the API uses, then processes `run_job` messages
enqueued by `forge.queue.enqueue_run` (webhook/schedule dispatch). Scale workers
horizontally; per-tenant concurrency is still bounded inside RunService.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from arq.connections import RedisSettings

from forge.config import settings

log = logging.getLogger("forge.worker")


async def run_job(ctx, run_id: str, tenant_id: str, project_id: str | None = None) -> dict:
    return await ctx["run_service"].run_to_completion(
        run_id=run_id, tenant_id=tenant_id, project_id=project_id
    )


async def _startup(ctx) -> None:
    from forge.main import _make_checkpointer
    from forge.services.runs import RunService

    stack = AsyncExitStack()
    ctx["_stack"] = stack
    checkpointer = await _make_checkpointer(stack)
    ctx["run_service"] = RunService(checkpointer=checkpointer, store=None)
    log.info("forge worker started (checkpoint backend=%s)", settings.checkpoint_backend)


async def _shutdown(ctx) -> None:
    await ctx["_stack"].aclose()


class WorkerSettings:
    functions = [run_job]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379")
