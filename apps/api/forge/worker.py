"""arq worker entrypoint - offloaded run execution (audit P1).

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

    # Fail-closed under the SAME hardening guard the API enforces in its lifespan. arq never
    # runs the FastAPI lifespan, so without this the worker would happily start under an unsafe
    # config (default secret / sqlite / non-durable checkpointer) the API refuses to serve.
    settings.ensure_dirs()
    problems = settings.validate_production()
    if problems:
        raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
    for warn in settings.startup_warnings():
        log.warning("INSECURE CONFIG: %s", warn)

    stack = AsyncExitStack()
    ctx["_stack"] = stack
    checkpointer = await _make_checkpointer(stack)
    ctx["run_service"] = RunService(checkpointer=checkpointer, store=None)
    log.info("forge worker started (checkpoint backend=%s)", settings.checkpoint_backend)


async def _shutdown(ctx) -> None:
    # _startup may have failed before setting _stack (e.g. the config guard raised); guard the
    # lookup so on_shutdown never masks the real startup error with a KeyError.
    stack = ctx.get("_stack")
    if stack is not None:
        await stack.aclose()


class WorkerSettings:
    functions = [run_job]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379")
