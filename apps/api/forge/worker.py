"""arq worker entrypoint - offloaded run execution (audit P1).

Run it (needs the `workers` + `postgres` extras and FORGE_REDIS_URL):

    arq forge.worker.WorkerSettings

It builds the SAME durable checkpointer the API uses, then processes `run_job` messages
enqueued by `forge.queue.enqueue_run` (webhook/schedule dispatch). Scale workers
horizontally; per-tenant concurrency is still bounded inside RunService.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack

from arq import Retry
from arq.connections import RedisSettings

from forge.config import settings

log = logging.getLogger("forge.worker")

# arq retry/backoff policy (finding k). A job that keeps failing is retried with exponential
# backoff up to MAX_TRIES, then dead-lettered instead of vanishing.
MAX_TRIES = 3
_DLQ_KEY = "forge:dlq:runs"      # Redis list of dead-lettered run jobs
_DLQ_MAX = 1000                  # keep the tail bounded


async def _dead_letter(ctx, run_id: str, tenant_id: str, project_id: str | None, err: Exception) -> None:
    """Record a permanently-failed run job (retries exhausted) to a bounded Redis list and mark
    the run errored so it doesn't linger as 'running' (the reaper is the backstop). Never raises."""
    record = {
        "run_id": run_id, "tenant_id": tenant_id, "project_id": project_id,
        "error": f"{type(err).__name__}: {err}", "tries": ctx.get("job_try"),
    }
    log.error("run %s dead-lettered after %s tries: %s", run_id, ctx.get("job_try"), err)
    try:
        redis = ctx.get("redis")
        if redis is not None:
            await redis.lpush(_DLQ_KEY, json.dumps(record, default=str))
            await redis.ltrim(_DLQ_KEY, 0, _DLQ_MAX - 1)
    except Exception:  # noqa: BLE001 - DLQ persistence is best-effort
        log.exception("failed to push run %s to the dead-letter queue", run_id)
    try:
        from forge.services.runs import RunService

        await RunService._mark_unfinished(run_id, tenant_id, status="error",
                                          error="dead-lettered: retries exhausted")
    except Exception:  # noqa: BLE001 - reaper will still resolve the stale run
        pass


async def run_job(ctx, run_id: str, tenant_id: str, project_id: str | None = None) -> dict:
    try:
        return await ctx["run_service"].run_to_completion(
            run_id=run_id, tenant_id=tenant_id, project_id=project_id
        )
    except Exception as e:  # noqa: BLE001 - retry with backoff, then dead-letter
        job_try = int(ctx.get("job_try", 1) or 1)
        if job_try >= MAX_TRIES:
            await _dead_letter(ctx, run_id, tenant_id, project_id, e)
            return {"status": "error", "dead_lettered": True, "error": str(e)}
        # Exponential backoff (capped) before the next attempt.
        raise Retry(defer=min(60, 2 ** job_try)) from e


async def _startup(ctx) -> None:
    from forge.main import _make_checkpointer
    from forge.services.runs import RunService

    # arq never runs the FastAPI lifespan, so apply the same IPv4-first DNS fix here - offloaded
    # runs make the same multi-hop outbound LLM/tool calls and would otherwise pay the AAAA stall.
    if settings.prefer_ipv4_egress:
        from forge.util.netfix import install_prefer_ipv4_dns

        install_prefer_ipv4_dns()
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
    # Retry a failing job up to MAX_TRIES with exponential backoff (raised as arq.Retry in
    # run_job); the final failure is dead-lettered rather than lost (finding k).
    max_tries = MAX_TRIES
