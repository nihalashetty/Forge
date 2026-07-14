"""SQL tool - let an agent run a parameterized, read-only query against a database.

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
# Read-only-breaking CONSTRUCTS the single-keyword list above can miss: writing query results to
# a file (SELECT ... INTO OUTFILE/DUMPFILE - a filesystem write / exfil vector) and row-locking
# clauses (FOR UPDATE/SHARE, LOCK IN SHARE MODE - side effects / lock contention). Matched as
# multi-word phrases so a column literally named `outfile` doesn't false-positive.
_FORBIDDEN_CONSTRUCTS = re.compile(
    r"\binto\s+(?:outfile|dumpfile)\b|\bfor\s+(?:update|share)\b|\block\s+in\s+share\s+mode\b",
    re.IGNORECASE,
)

# Per-cell size ceiling so one giant TEXT/BLOB column can't blow the tool observation / model
# context. Wanted setting: `sql_tool_max_cell_chars` (default 10000); a module constant for now.
_MAX_CELL_CHARS = 10_000


class SqlToolError(RuntimeError):
    pass


async def _engine_for(url: str):
    eng = _ENGINE_CACHE.get(url)
    if eng is None:
        if len(_ENGINE_CACHE) >= _ENGINE_CACHE_MAX:
            # Evict the oldest entry and DISPOSE its pool so pooled connections / sqlite file
            # handles are released now, rather than left to GC (a bare dict.clear() leaks them).
            old_url = next(iter(_ENGINE_CACHE))
            old = _ENGINE_CACHE.pop(old_url, None)
            if old is not None:
                try:
                    await old.dispose()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
        eng = create_async_engine(url, pool_pre_ping=True)
        _ENGINE_CACHE[url] = eng
    return eng


def _cap_cell(v: Any) -> Any:
    """Truncate an oversized string/bytes cell so a single huge column can't bloat the result."""
    if isinstance(v, str) and len(v) > _MAX_CELL_CHARS:
        return v[:_MAX_CELL_CHARS] + f"…[truncated {len(v) - _MAX_CELL_CHARS} chars]"
    if isinstance(v, (bytes, bytearray)) and len(v) > _MAX_CELL_CHARS:
        return f"<{len(v)} bytes; truncated>"
    return v


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
    if _FORBIDDEN_CONSTRUCTS.search(q):
        raise SqlToolError("query uses a non-read-only construct (INTO OUTFILE/DUMPFILE or a row-lock clause); read-only tools forbid it")


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
    # allow/deny/block_private), passed through exactly like REST/GraphQL get it - use it
    # directly so the SQL DSN guard honors per-project overrides. Only fall back to building
    # from settings when given a raw dict / None.
    policy = egress if isinstance(egress, EgressPolicy) else EgressPolicy.from_settings(egress if isinstance(egress, dict) else None)
    await _assert_dsn_allowed(url, policy)

    engine = await _engine_for(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    data: list[dict] = []
    truncated = False
    async with sm() as session:
        trans = await session.begin()
        try:
            # Belt-and-suspenders read-only at the DB layer (not just the regex): Postgres
            # honours a read-only transaction even for volatile/SECURITY DEFINER functions.
            if read_only and engine.dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
            # Stream rows and STOP after max_rows instead of materializing the whole result set
            # then slicing: a query matching millions of rows would otherwise buffer every row
            # into memory before we throw the tail away. We peek one past max_rows to set
            # `truncated` accurately (there really were more rows), without keeping that row.
            result = await session.stream(text(query), kwargs or {})
            try:
                async for row in result.mappings():
                    if len(data) >= max_rows:
                        truncated = True
                        break
                    data.append({k: _cap_cell(v) for k, v in dict(row).items()})
            finally:
                await result.close()
        finally:
            await trans.rollback()  # read-only: never persist
    return {"rows": data, "row_count": len(data), "truncated": truncated}


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
