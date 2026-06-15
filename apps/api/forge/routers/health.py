"""Health & version."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter

import forge

router = APIRouter(tags=["meta"])


def _v(pkg: str) -> str:
    try:
        return version(pkg)
    except PackageNotFoundError:
        return "not-installed"


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/version")
async def version_info() -> dict:
    return {
        "name": "forge-api",
        "version": forge.__version__,
        "langchain": _v("langchain"),
        "langgraph": _v("langgraph"),
    }
