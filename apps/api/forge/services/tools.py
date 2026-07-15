"""Tool CRUD + the /test endpoint (raw vs projected payload + token delta)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.auth_providers.resolver import AuthResolver
from forge.models import Tool
from forge.secrets.store import SecretStore
from forge.tools.graphql import execute_graphql
from forge.tools.projection import estimate_tokens
from forge.tools.rest import execute_rest, project_observation


class ToolService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str) -> list[Tool]:
        rows = await session.execute(
            select(Tool).where(Tool.tenant_id == tenant_id, Tool.project_id == project_id)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, tool_id: str) -> Tool | None:
        row = await session.execute(select(Tool).where(Tool.tenant_id == tenant_id, Tool.id == tool_id))
        return row.scalar_one_or_none()

    @staticmethod
    async def create(session: AsyncSession, tenant_id: str, project_id: str, *, name: str, kind: str, config: dict, auth_provider_id: str | None = None) -> Tool:
        tool = Tool(
            tenant_id=tenant_id, project_id=project_id, name=name, kind=kind,
            config=config or {}, auth_provider_id=auth_provider_id or (config or {}).get("auth_provider_id"),
        )
        session.add(tool)
        await session.commit()
        await session.refresh(tool)
        return tool

    @staticmethod
    async def update(session: AsyncSession, tool: Tool, *, name: str | None = None, config: dict | None = None,
                     auth_provider_id: str | None = None, enabled: bool | None = None) -> Tool:
        if name is not None:
            tool.name = name
        if config is not None:
            tool.config = config
        if auth_provider_id is not None:
            tool.auth_provider_id = auth_provider_id or None
        if enabled is not None:
            tool.enabled = enabled
        await session.commit()
        await session.refresh(tool)
        return tool

    @staticmethod
    async def delete(session: AsyncSession, tool: Tool) -> None:
        """Delete a tool. Agents/workflows reference tools by id in their config; the
        compiler tolerates missing ids (tools_for skips them), so deletion is safe."""
        await session.delete(tool)
        await session.commit()

    @staticmethod
    async def record_test(session: AsyncSession, tool: Tool, result: dict) -> None:
        """Persist the last test (token delta + clipped payloads) on the tool, so the
        builder shows the real last raw/projected response on reopen instead of the
        sample placeholder."""
        import json as _json
        from datetime import datetime

        def _clip(value, limit=20_000):
            """Keep stored payloads bounded; oversized ones become truncated JSON text."""
            try:
                s = _json.dumps(value, default=str)
            except Exception:  # noqa: BLE001
                return str(value)[:limit]
            return value if len(s) <= limit else s[:limit] + "… (truncated)"

        cfg = dict(tool.config or {})
        if result.get("ok"):
            cfg["_last_test"] = {
                "raw_tokens": result.get("raw_tokens"),
                "projected_tokens": result.get("projected_tokens"),
                "status": result.get("status"),
                "latency_ms": result.get("latency_ms"),
                "raw": _clip(result.get("raw")),
                "projected": _clip(result.get("projected")),
                "final_url": result.get("final_url"),
                "redirect": result.get("redirect"),
                "at": datetime.utcnow().isoformat(),
            }
            tool.last_tested = "pass"
        else:
            tool.last_tested = "fail"
        tool.config = cfg
        await session.commit()

    @staticmethod
    async def test(tenant_id: str, project_id: str, cfg: dict, args: dict, context: dict | None = None) -> dict:
        resolver = AuthResolver(SecretStore())
        kind = cfg.get("kind")
        try:
            if kind == "rest_api":
                res = await execute_rest(cfg, args, tenant_id=tenant_id, project_id=project_id, context=context, auth_resolver=resolver)
            elif kind == "graphql":
                res = await execute_graphql(cfg, args, tenant_id=tenant_id, project_id=project_id, context=context, auth_resolver=resolver)
            elif kind in ("builtin", "code", "sql"):
                from forge.services.runtime import make_runtime_ctx
                ctx = make_runtime_ctx(tenant_id, project_id)
                if kind == "builtin":
                    from forge.tools.builtin import build_builtin_tool
                    tool = build_builtin_tool(cfg, ctx)
                elif kind == "code":
                    from forge.tools.code import build_code_tool
                    tool = build_code_tool(cfg, ctx)
                else:
                    from forge.tools.sql import build_sql_tool
                    tool = build_sql_tool(cfg, ctx)
                out = await tool.ainvoke(args)
                res = {"raw": out, "projected": out, "status": 200, "latency_ms": 0}
            else:
                return {"ok": False, "error": f"Test {kind!r} tools by attaching them to an agent and running the workflow."}
        except Exception as e:  # noqa: BLE001 - surface failures (network/auth/etc.) to the UI
            return {"ok": False, "error": str(e)}

        # Preview exactly what the agent receives (rest.project_observation): the projection is
        # applied to the model observation, which on a redirect is the {"body", "redirect"} envelope
        # carrying the target URL. So an un-projected 3xx no longer reads as "" / 0 tok, and a
        # `redirect.location` projection previews as just the URL - with the meter counting both.
        observation, projected = project_observation(res, cfg)
        return {
            "ok": True,
            "status": res.get("status"),
            "latency_ms": res.get("latency_ms"),
            "raw": observation,
            "projected": projected,
            "raw_tokens": estimate_tokens(observation),
            "projected_tokens": estimate_tokens(projected),
            "final_url": res.get("final_url"),
            "redirect": res.get("redirect"),
        }
