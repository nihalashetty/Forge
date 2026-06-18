"""SQL tool — let an agent run a parameterized, read-only query against a database.

The connection string comes from a secret (`connection_ref` → `secret://proj/...`) so
credentials never live in tool config. Read-only is enforced by default: only a single
SELECT/WITH statement, no statement chaining, and the work runs in a rolled-back
transaction. Results are capped at `max_rows`.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from forge.secrets.store import SecretStore
from forge.util.ssrf import EgressPolicy, validate_host_port

_ENGINE_CACHE: dict[str, Any] = {}
_ENGINE_CACHE_MAX = 64
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|merge|replace|call|exec|execute|attach|pragma|vacuum)\b",
    re.IGNORECASE,
)


class SqlToolError(RuntimeError):
    pass


def _engine_for(url: str):
    eng = _ENGINE_CACHE.get(url)
    if eng is None:
        if len(_ENGINE_CACHE) >= _ENGINE_CACHE_MAX:
            _ENGINE_CACHE.clear()  # crude bound; engines are GC'd / pools recycled
        eng = create_async_engine(url, pool_pre_ping=True)
        _ENGINE_CACHE[url] = eng
    return eng


async def _assert_dsn_allowed(url: str, policy: EgressPolicy | None = None) -> None:
    """Run the DSN's network host through the egress (SSRF) policy so a SQL tool can't be
    pointed at an internal/loopback/metadata database, bypassing the HTTP egress guard
    (audit S7). Local sqlite files (no host) are allowed."""
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - if we can't parse it, don't try to connect
        raise SqlToolError("invalid database connection URL") from None
    await validate_host_port(parsed.host, parsed.port or 0, policy)


def _assert_read_only(query: str) -> None:
    q = query.strip().rstrip(";").strip()
    if ";" in q:
        raise SqlToolError("multiple statements are not allowed in a read-only SQL tool")
    head = q.split(None, 1)[0].lower() if q else ""
    if head not in ("select", "with"):
        raise SqlToolError("read-only SQL tools may only run SELECT / WITH queries")
    if _FORBIDDEN.search(q):
        raise SqlToolError("query contains a write/DDL keyword; read-only tools forbid it")


async def execute_sql(
    cfg: dict, kwargs: dict, *, tenant_id: str, project_id: str, egress: Any = None,
) -> dict:
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

    # ctx.egress_policy is a resolved EgressPolicy INSTANCE (merges global + per-project
    # allow/deny/block_private), passed through exactly like REST/GraphQL get it — use it
    # directly so the SQL DSN guard honors per-project overrides. Only fall back to building
    # from settings when given a raw dict / None.
    policy = egress if isinstance(egress, EgressPolicy) else EgressPolicy.from_settings(egress if isinstance(egress, dict) else None)
    await _assert_dsn_allowed(url, policy)

    engine = _engine_for(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        trans = await session.begin()
        try:
            # Belt-and-suspenders read-only at the DB layer (not just the regex): Postgres
            # honours a read-only transaction even for volatile/SECURITY DEFINER functions.
            if read_only and engine.dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
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
        res = await execute_sql(
            cfg, kwargs, tenant_id=ctx.tenant_id, project_id=ctx.project_id,
            egress=getattr(ctx, "egress_policy", None),
        )
        return res["rows"]

    return StructuredTool.from_function(
        coroutine=_call, name=cfg["name"], description=cfg.get("description", ""), args_schema=args_schema,
    )
