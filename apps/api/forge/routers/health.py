"""Health, readiness, version, and Prometheus metrics."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

import forge
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


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: can this instance actually serve traffic? Checks the DB and that the
    durable checkpointer is wired. Returns 503 (not 200) when a dependency is down so a
    load balancer / k8s readiness probe routes around it (audit P-imp)."""
    checks: dict[str, str] = {}
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"error: {type(e).__name__}"
    checks["checkpointer"] = "ok" if getattr(request.app.state, "checkpointer", None) is not None else "missing"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse({"ready": ready, "checks": checks}, status_code=200 if ready else 503)


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus text-format exposition of the in-process counters so operators can scrape
    them (per-worker; aggregate across replicas at the scraper). Complements the OTLP trace
    export. No new dependency - rendered directly from the counter snapshot."""
    lines: list[str] = []
    for name, value in sorted(snapshot().items()):
        metric = "forge_" + "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
        lines.append(f"# TYPE {metric} counter")
        lines.append(f"{metric} {value}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/version")
async def version_info() -> dict:
    return {
        "name": "forge-api",
        "version": forge.__version__,
        "langchain": _v("langchain"),
        "langgraph": _v("langgraph"),
    }
