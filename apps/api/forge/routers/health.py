"""Health, readiness, version, and Prometheus metrics."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

import forge
from forge.config import settings
from forge.db import SessionLocal
from forge.util.metrics import snapshot

router = APIRouter(tags=["meta"])


def _v(pkg: str) -> str:
    try:
        return version(pkg)
    except PackageNotFoundError:
        return "not-installed"


@router.get("/health")
@router.get("/livez")
async def health() -> dict:
    """Liveness: the process is up and serving. Cheap and dependency-free."""
    return {"status": "ok"}


def _ping_redis() -> str:
    """Ping Redis and confirm a worker heartbeat when arq is in use. Returns an 'ok'/'error'/…
    status string; only called when FORGE_REDIS_URL is set."""
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
        client.ping()
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}"
    return "ok"


def _worker_status() -> str:
    """Best-effort arq worker liveness via its health-check key in Redis (arq writes
    `arq:health-check`). 'missing' means Redis is up but no worker has checked in."""
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
        return "ok" if client.exists("arq:health-check") else "missing"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}"


def _vector_status() -> str:
    try:
        import chromadb

        chromadb.PersistentClient(path=settings.chroma_path).heartbeat()
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}"
    return "ok"


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: can this instance actually serve traffic? Checks the DB, the durable
    checkpointer, and (when configured) Redis, the vector store, and a worker heartbeat. Returns
    503 when a GATING dependency is down so a load balancer / k8s probe routes around it
    (audit P-imp / finding k). The worker is reported but non-gating - the API can still serve
    while the worker tier is down (runs queue)."""
    checks: dict[str, str] = {}
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"error: {type(e).__name__}"
    checks["checkpointer"] = "ok" if getattr(request.app.state, "checkpointer", None) is not None else "missing"
    checks["vector_store"] = _vector_status()  # reported; non-gating (API serves w/o knowledge)
    # Gating checks: everything that must be healthy for this instance to serve.
    gating = ["db", "checkpointer"]
    if settings.redis_url:
        checks["redis"] = _ping_redis()
        checks["worker"] = _worker_status()  # reported, not gating
        gating.append("redis")
    ready = all(checks.get(k) == "ok" for k in gating)
    return JSONResponse({"ready": ready, "checks": checks}, status_code=200 if ready else 503)


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus text-format exposition of the in-process counters so operators can scrape
    them (per-worker; aggregate across replicas at the scraper). Complements the OTLP trace
    export. No new dependency - rendered directly from the counter snapshot.

    Gated behind `settings.expose_metrics` (default off): the counters are an internal
    operational surface, so keep it disabled on public deployments and enable only where the
    scrape endpoint is reachable from a trusted network."""
    if not settings.expose_metrics:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    lines: list[str] = []
    for name, value in sorted(snapshot().items()):
        metric = "forge_" + "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
        lines.append(f"# TYPE {metric} counter")
        lines.append(f"{metric} {value}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/version")
async def version_info() -> dict:
    # Dependency versions aid fingerprinting; gate behind the same operator-only switch as /metrics.
    if not settings.expose_metrics:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return {
        "name": "forge-api",
        "version": forge.__version__,
        "langchain": _v("langchain"),
        "langgraph": _v("langgraph"),
    }
