"""Assemble a CompileContext for a run: resolver + materialized project tools.

Tools reference `ctx.auth_resolver` by closure, so we create the context first
(with the resolver) and then materialize tools into it. Unimplemented/broken tool
kinds are skipped so a run still compiles.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from forge.auth_providers.resolver import AuthResolver
from forge.config import settings
from forge.engine.context import CompileContext
from forge.engine.models import default_model_for_credentials
from forge.models import Agent, Project, Tool, Workflow
from forge.secrets.store import SecretStore
from forge.tools.materialize import materialize_tool
from forge.util.ssrf import EgressPolicy

log = logging.getLogger("forge.runtime")


def make_runtime_ctx(tenant_id: str, project_id: str, *, default_model: str | None = None) -> CompileContext:
    return CompileContext(
        tenant_id=tenant_id,
        project_id=project_id,
        auth_resolver=AuthResolver(SecretStore()),
        default_model=default_model or settings.default_model,
    )


def _tool_cfg(t: Tool) -> dict:
    cfg = dict(t.config or {})
    cfg.setdefault("name", t.name)
    cfg.setdefault("kind", t.kind)
    if t.auth_provider_id and not cfg.get("auth_provider_id"):
        cfg["auth_provider_id"] = t.auth_provider_id
    return cfg


async def build_compile_context(
    session, *, tenant_id: str, project_id: str, checkpointer=None, store=None
) -> CompileContext:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    pconfig = (project.config or {}) if project else {}

    secret_store = SecretStore()

    # Resolve provider API keys (provider -> secret ref) to plaintext for this run only.
    resolved_keys: dict[str, str] = {}
    for provider, ref in (pconfig.get("provider_credentials") or {}).items():
        try:
            val = await secret_store.read_ref(tenant_id=tenant_id, project_id=project_id, ref=ref)
            resolved_keys[provider] = val if isinstance(val, str) else (val.get("key") or val.get("value") or str(val)) if isinstance(val, dict) else str(val)
        except Exception as e:  # noqa: BLE001 - missing/invalid key just falls back to env
            log.warning("Provider key %s unresolved: %s", provider, e)

    # Pick the run's default model. An explicit (non-fake) project default wins; else,
    # if the project has a provider key, default to that provider's model instead of
    # silently degrading agent nodes with no model to the offline `fake:` model.
    explicit = pconfig.get("default_model")
    if explicit and not str(explicit).startswith("fake"):
        default_model = explicit
    else:
        default_model = default_model_for_credentials(resolved_keys) or explicit or settings.default_model

    ctx = CompileContext(
        tenant_id=tenant_id,
        project_id=project_id,
        checkpointer=checkpointer,
        store=store,
        auth_resolver=AuthResolver(secret_store),
        default_model=default_model,
        project_default_mw=pconfig.get("default_middleware", []) or [],
    )
    ctx.provider_credentials = resolved_keys
    ctx.egress_policy = EgressPolicy.from_settings(pconfig.get("egress"))

    rows = (
        await session.execute(
            select(Tool).where(
                Tool.tenant_id == tenant_id, Tool.project_id == project_id, Tool.enabled.is_(True)
            )
        )
    ).scalars()
    registry: dict[str, object] = {}
    specs: dict[str, dict] = {}
    for t in rows:
        cfg = _tool_cfg(t)
        try:
            tool = materialize_tool(cfg, ctx)
            registry[t.id] = tool
            specs[t.id] = {"kind": t.kind, "config": cfg, "tool": tool}
        except Exception as e:  # noqa: BLE001 - skip unimplemented/broken tools
            from forge.util.metrics import incr

            incr("tool.materialize_failed", detail=f"{t.name} ({t.kind}): {e}")
            log.warning("Skipping tool %s (%s): %s", t.name, t.kind, e)
    ctx.tool_registry = registry
    ctx.tool_specs = specs

    # Pre-load enabled MCP servers' tools (one connect per server) so agent nodes can
    # attach them — the agent factory is sync, but MCP discovery is async.
    from forge.models import McpClient
    from forge.tools.mcp import server_tools

    mcp_by_client: dict[str, list] = {}
    mcp_rows = (
        await session.execute(
            select(McpClient).where(
                McpClient.tenant_id == tenant_id,
                McpClient.project_id == project_id,
                McpClient.enabled.is_(True),
            )
        )
    ).scalars()
    for m in mcp_rows:
        try:
            mcp_by_client[m.id] = await server_tools(m, tenant_id, project_id)
        except Exception as e:  # noqa: BLE001 - an unreachable server must not break the run
            log.warning("MCP server %s unavailable, skipping its tools: %s", m.name, e)
    ctx.mcp_tools_by_client = mcp_by_client

    # Saved agent presets, so an agent node with `agent_ref` mirrors the live preset.
    agent_rows = (
        await session.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.project_id == project_id)
        )
    ).scalars()
    ctx.agent_presets = {a.id: (a.config or {}) for a in agent_rows}

    # Workflow executables (keyed by id AND name) so `subworkflow` nodes can reference
    # another workflow as a reusable component.
    wf_rows = (
        await session.execute(
            select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.project_id == project_id)
        )
    ).scalars()
    wf_map: dict[str, dict] = {}
    for w in wf_rows:
        if w.executable:
            wf_map[w.id] = w.executable
            wf_map.setdefault(w.name, w.executable)
    ctx.workflows = wf_map
    return ctx
