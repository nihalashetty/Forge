"""FastAPI application factory + lifespan.

Builds our own server on the MIT LangChain/LangGraph framework — never depends on
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
    projects,
    runs,
    secrets,
    stats,
    tools,
    traces,
    workflows,
)
from forge.routers import (
    triggers as triggers_router,
)
from forge.util.http import aclose_shared_client


async def _make_checkpointer(stack: AsyncExitStack):
    """Durable execution. SQLite locally; set FORGE_CHECKPOINT_DB=memory for ephemeral."""
    if settings.checkpoint_db == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    cp = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(settings.checkpoint_db))
    try:
        await cp.setup()
    except Exception:  # noqa: BLE001 - setup is idempotent; ignore "already exists"
        pass
    return cp


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
        # SSRF guard off, SQLite). Set the flagged env vars before deploying.
        raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
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
    scheduler = None
    if settings.enable_scheduler:
        scheduler = asyncio.create_task(_scheduler_loop(app), name="forge-scheduler")
    yield
    if scheduler is not None:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
    await aclose_shared_client()
    await app.state.exit_stack.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Forge API",
        version=forge.__version__,
        description="Self-hosted platform for building, testing, and shipping LangChain/LangGraph agents.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Audit all successful mutations (pure ASGI; safe with SSE streams).
    from forge.audit_middleware import AuditMiddleware

    app.add_middleware(AuditMiddleware)
    for r in (
        health.router, auth.router, auth.team_router, audit.router, oauth.router, hooks.router,
        nodes.router, projects.router, workflows.router, runs.router,
        tools.router, auth_providers.router, secrets.router, agents.router,
        knowledge.router, knowledge.qa_router, traces.router, assistant.router, stats.router,
        triggers_router.router, channels.router, channels.public, handoff.router, evals.router,
        pricing.router, mcp_server.router, mcp_clients.router,
    ):
        app.include_router(r)
    return app


app = create_app()
