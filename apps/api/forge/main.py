"""FastAPI application factory + lifespan.

Builds our own server on the MIT LangChain/LangGraph framework - never depends on
`langgraph-api` or LangSmith. Lifespan initializes the DB, the durable-execution
checkpointer, and dev seed data.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import forge
from forge.config import settings
from forge.db import SessionLocal, init_db
from forge.db.seed import bootstrap, seed_demo_data
from forge.routers import (
    agents,
    assistant,
    audit,
    auth,
    auth_providers,
    channels,
    components,
    conversations,
    embed,
    embed_public,
    evals,
    handoff,
    health,
    hooks,
    knowledge,
    mcp_clients,
    mcp_server,
    nodes,
    oauth,
    pricing,
    project_run,
    projects,
    runs,
    secrets,
    stats,
    tools,
    traces,
    versions,
    workflows,
)
from forge.routers import (
    triggers as triggers_router,
)
from forge.util.http import aclose_shared_client


async def _make_checkpointer(stack: AsyncExitStack):
    """Durable-execution checkpointer. Selected by FORGE_CHECKPOINT_BACKEND:
    - "postgres": durable + shared across workers (REQUIRED for prod/HITL; audit P2).
    - "memory": ephemeral (tests / throwaway).
    - "sqlite" (default): local file; fine for single-worker dev, lost on restart."""
    backend = (settings.checkpoint_backend or "sqlite").lower()
    if backend == "memory" or settings.checkpoint_db == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    if backend == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as e:  # pragma: no cover - optional extra
            raise RuntimeError(
                "FORGE_CHECKPOINT_BACKEND=postgres needs langgraph-checkpoint-postgres "
                "(pip install -e '.[postgres]')."
            ) from e
        dsn = settings.checkpoint_postgres_url or settings.database_url
        # LangGraph wants a plain libpq DSN, not the SQLAlchemy +asyncpg/+psycopg form.
        for prefix in ("+asyncpg", "+psycopg", "+psycopg2"):
            dsn = dsn.replace(prefix, "")
        cp = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(dsn))
        try:
            await cp.setup()
        except Exception:  # noqa: BLE001 - setup is idempotent
            pass
        return cp
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    cp = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(settings.checkpoint_db))
    try:
        await cp.setup()
    except Exception:  # noqa: BLE001 - setup is idempotent; ignore "already exists"
        pass
    return cp


async def _reaper_loop() -> None:
    """Periodically reap runs stuck in queued/running (never streamed, or driver died) so
    they can't linger forever (audit F3)."""
    from forge.services.runs import RunService

    log = logging.getLogger("forge.reaper")
    while True:
        try:
            await asyncio.sleep(300)
            await RunService.reap_stale_runs()
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001 - keep the reaper alive across failures
            log.exception("reaper tick failed")


async def _retention_loop() -> None:
    """Purge traces/spans/runs past each project's retention horizon and audit logs past the
    workspace horizon, on a timer (finding e). Leader-only (like the reaper) so a multi-replica
    deployment purges once. No-op unless a retention window is configured."""
    from forge.services.retention import RetentionService

    log = logging.getLogger("forge.retention")
    interval = max(60, int(settings.retention_interval_seconds or 3600))
    while True:
        try:
            await asyncio.sleep(interval)
            await RetentionService.purge_expired()
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001 - keep the retention loop alive across failures
            log.exception("retention tick failed")


async def _scheduler_loop(app: FastAPI) -> None:
    """Fire due `schedule` triggers once a minute. Single-worker in-process scheduler;
    for multi-worker prod, move to arq/Redis (FORGE_REDIS_URL) so it runs once globally."""
    from forge.services.dispatch import run_due_app_events, run_due_schedules
    from forge.services.runs import RunService

    log = logging.getLogger("forge.scheduler")
    run_service = RunService(checkpointer=app.state.checkpointer, store=app.state.store)
    while True:
        try:
            await asyncio.sleep(60)
            await run_due_schedules(run_service)
            await run_due_app_events(run_service)
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001 - keep the scheduler alive across failures
            log.exception("scheduler tick failed")


def _preload_heavy_modules() -> None:
    """Import the slow modules off the critical path. First import of langchain_openai
    / chromadb costs ~22s / ~9s on this machine (AV scanning); doing it in a daemon
    thread at startup means the first real run doesn't pay it."""
    import importlib

    for mod in ("langchain_openai", "chromadb", "langchain.chat_models"):
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001 - optional providers may be missing
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    problems = settings.validate_production()
    if problems:
        # Refuse to serve a misconfigured production install (default secrets, auth off,
        # SSRF guard off, SQLite, non-durable checkpointer, unsandboxed code). Set the
        # flagged env vars before deploying. Enforced for every non-dev environment (S6).
        raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
    for warn in settings.startup_warnings():
        logging.getLogger("forge.config").warning("INSECURE CONFIG: %s", warn)
    await init_db()
    threading.Thread(target=_preload_heavy_modules, name="forge-preload", daemon=True).start()
    app.state.exit_stack = AsyncExitStack()
    app.state.checkpointer = await _make_checkpointer(app.state.exit_stack)
    app.state.store = None
    async with SessionLocal() as session:
        tenant_id = await bootstrap(session)
        if settings.seed_demo:
            await seed_demo_data(session, tenant_id)
        app.state.tenant_id = tenant_id
        from forge.routers.pricing import load_pricing_overrides

        await load_pricing_overrides(session)
    if settings.otel_enabled:
        from forge.tracing import otel

        otel.configure()
    bg_tasks: list[asyncio.Task] = []
    # The scheduler must run on EXACTLY ONE instance (else every replica double-fires).
    # `enable_scheduler` turns it on; `scheduler_leader` elects the single instance by env
    # so you can ship one image everywhere (audit P3).
    if settings.enable_scheduler and settings.scheduler_leader:
        bg_tasks.append(asyncio.create_task(_scheduler_loop(app), name="forge-scheduler"))
    # The reaper is safe to run everywhere (idempotent), but one instance is enough.
    if settings.scheduler_leader:
        bg_tasks.append(asyncio.create_task(_reaper_loop(), name="forge-reaper"))
    # Data-retention purge (leader-only): ages out traces/spans/runs + audit logs (finding e).
    if settings.enable_retention and settings.scheduler_leader:
        bg_tasks.append(asyncio.create_task(_retention_loop(), name="forge-retention"))
    yield
    for t in bg_tasks:
        t.cancel()
    for t in bg_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
    from forge.util.tasks import drain

    await drain()
    with contextlib.suppress(Exception):
        from forge.tools.mcp import close_all

        await close_all()
    with contextlib.suppress(Exception):
        from forge.queue import close_pool

        await close_pool()
    await aclose_shared_client()
    await app.state.exit_stack.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Forge API",
        version=forge.__version__,
        description="Self-hosted platform for building, testing, and shipping LangChain/LangGraph agents.",
        lifespan=lifespan,
    )
    # Host-header allow-list (defense-in-depth against Host-header attacks). Empty => any (dev).
    if settings.trusted_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Coarse per-IP request ceiling (api_rate_limit_per_minute) as a blunt DoS guard;
    # complements the per-surface limits. Health/SSE exempt (finding a).
    if settings.enable_global_rate_limit:
        from forge.util.ratelimit import GlobalRateLimitMiddleware

        app.add_middleware(GlobalRateLimitMiddleware)
    # Audit all successful mutations (pure ASGI; safe with SSE streams).
    from forge.audit_middleware import AuditMiddleware

    app.add_middleware(AuditMiddleware)
    for r in (
        health.router, auth.router, auth.team_router, auth.workspace_router, auth.apikeys_router,
        audit.router, oauth.router, hooks.router,
        nodes.router, projects.router, workflows.router, runs.router, project_run.router,
        tools.router, components.router, embed.router, embed_public.router, auth_providers.router, secrets.router, agents.router,
        knowledge.router, knowledge.qa_router, traces.router, conversations.router, assistant.router, stats.router,
        triggers_router.router, channels.router, channels.public, handoff.router, evals.router,
        pricing.router, mcp_server.router, mcp_clients.router, versions.router,
    ):
        app.include_router(r)
    return app


app = create_app()
