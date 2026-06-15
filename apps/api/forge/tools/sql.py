"""SQL tool — let an agent run a parameterized, read-only query against a database.

The connection string comes from a secret (`connection_ref` → `secret://proj/...`) so
credentials never live in tool config. Read-only is enforced by default: only a single
SELECT/WITH statement, no statement chaining, and the work runs in a rolled-back
transaction. Results are capped at `max_rows`.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from forge.secrets.store import SecretStore

_ENGINE_CACHE: dict[str, Any] = {}
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|merge|replace|call|exec|execute|attach|pragma|vacuum)\b",
    re.IGNORECASE,
)


class SqlToolError(RuntimeError):
    pass


def _engine_for(url: str):
    eng = _ENGINE_CACHE.get(url)
    if eng is None:
        eng = create_async_engine(url, pool_pre_ping=True)
        _ENGINE_CACHE[url] = eng
    return eng


def _assert_read_only(query: str) -> None:
    q = query.strip().rstrip(";").strip()
    if ";" in q:
        raise SqlToolError("multiple statements are not allowed in a read-only SQL tool")
    head = q.split(None, 1)[0].lower() if q else ""
    if head not in ("select", "with"):
        raise SqlToolError("read-only SQL tools may only run SELECT / WITH queries")
    if _FORBIDDEN.search(q):
        raise SqlToolError("query contains a write/DDL keyword; read-only tools forbid it")


async def execute_sql(cfg: dict, kwargs: dict, *, tenant_id: str, project_id: str) -> dict:
    query = cfg.get("query") or ""
    read_only = cfg.get("read_only", True)
    max_rows = int(cfg.get("max_rows", 100))
    if read_only:
        _assert_read_only(query)

    ref = cfg.get("connection_ref")
    url = cfg.get("connection_url")
    if ref:
        val = await SecretStore().read_ref(tenant_id=tenant_id, project_id=project_id, ref=ref)
        url = val if isinstance(val, str) else (val.get("url") or val.get("dsn")) if isinstance(val, dict) else url
    if not url:
        raise SqlToolError("no database connection configured (set connection_ref to a secret holding the URL)")

    engine = _engine_for(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        trans = await session.begin()
        try:
            result = await session.execute(text(query), kwargs or {})
            rows = result.mappings().all()
            data = [dict(r) for r in rows[:max_rows]]
        finally:
            await trans.rollback()  # read-only: never persist
    return {"rows": data, "row_count": len(data), "truncated": len(data) >= max_rows}


def build_sql_tool(cfg: dict, ctx):
    from langchain_core.tools import StructuredTool

    from forge.tools.rest import build_args_schema_from_jsonschema

    args_schema = build_args_schema_from_jsonschema(
        cfg.get("args_schema") or {}, name=f"{cfg.get('name', 'sql')}_args"
    )

    async def _call(**kwargs):
        res = await execute_sql(cfg, kwargs, tenant_id=ctx.tenant_id, project_id=ctx.project_id)
        return res["rows"]

    return StructuredTool.from_function(
        coroutine=_call, name=cfg["name"], description=cfg.get("description", ""), args_schema=args_schema,
    )
