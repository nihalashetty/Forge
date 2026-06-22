"""CompileContext - the per-compile dependency bundle (Doc 2 §6).

Carries everything `NodeSpec.factory` / `MW_BUILDERS` need: tenant scoping, the
checkpointer + store, the tracer callback, the materialized tool registry, the auth
resolver, the sandbox, and model-provider credential bindings. Kept dependency-light
(plain dataclass with optionals) so the engine core is unit-testable in isolation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompileContext:
    tenant_id: str
    project_id: str

    # LangGraph durability + long-term memory.
    checkpointer: Any = None
    store: Any = None

    # Tracing callback handler attached to every astream/ainvoke.
    tracer: Any = None

    # Materialized tools: tool_id -> StructuredTool (built by tools.materialize).
    tool_registry: dict[str, Any] = field(default_factory=dict)
    # tool_id -> {"kind", "config", "tool"} so the tool_call node can invoke directly.
    tool_specs: dict[str, dict] = field(default_factory=dict)
    # MCP server id -> list of native LangChain tools (the server's enabled tools),
    # pre-loaded by the runtime assembler so the sync agent factory can attach them.
    mcp_tools_by_client: dict[str, list] = field(default_factory=dict)
    # Materialized UI components (component_id -> widget StructuredTool); attached to an
    # agent via config["components"], the same way tools are (Feature 2 - generative UI).
    component_registry: dict[str, Any] = field(default_factory=dict)

    # Cross-cutting services.
    auth_resolver: Any = None
    sandbox: Any = None

    # SSRF egress policy (project override of the global allow/deny + private-range
    # block), applied to every outbound HTTP call a workflow makes (tools, webhooks,
    # web_fetch). Set by the runtime assembler from project.config.egress.
    egress_policy: Any = None

    # Model config.
    default_model: str | None = None
    provider_credentials: dict[str, str] = field(default_factory=dict)

    # The end user this run acts for (identity, Feature 3). Generic app-defined shape
    # ({id, roles?, attributes?, entitlements?, …}); surfaced to agent prompts (awareness)
    # and tool templating ({{ctx.end_user…}} / on-behalf-of calls). None = anonymous.
    end_user: dict | None = None

    # Project-level default middleware, prepended to every agent stack (Doc 2 §8).
    project_default_mw: list[dict] = field(default_factory=list)

    # Saved agent presets (agent_id -> config), so an agent node can mirror one by
    # `agent_ref` and pick up edits made in the Agents tab without re-saving the workflow.
    agent_presets: dict[str, dict] = field(default_factory=dict)

    # Project workflows' executables (id -> definition) so a `subworkflow` node can
    # compile a referenced workflow as a nested graph. `compiling` tracks in-progress
    # ids to break recursion cycles.
    workflows: dict[str, dict] = field(default_factory=dict)
    compiling: set = field(default_factory=set)

    def tools_for(self, ids: Sequence[str]) -> list[Any]:
        """Resolve tool ids to materialized tools, skipping unknown ids.

        Unknown ids are tolerated at compile time and surfaced by the validator
        instead, so a partially-wired draft still compiles for preview.
        """
        out = []
        for i in ids or []:
            tool = self.tool_registry.get(i)
            if tool is not None:
                out.append(tool)
        return out

    def components_for(self, ids: Sequence[str]) -> list[Any]:
        """Resolve component ids to materialized widget-tools, skipping unknown ids
        (a deleted component just drops out, like tools_for)."""
        out = []
        for i in ids or []:
            tool = self.component_registry.get(i)
            if tool is not None:
                out.append(tool)
        return out

    def has_entitlements(self, required) -> bool:
        """True if the run's end_user holds ALL of `required` (matched against roles ∪
        entitlements). Empty/absent requirement → allowed, anonymous user → denied. The
        server-side gate for tools that declare `required_entitlements` (Feature 3b)."""
        req = [r for r in (required or []) if r]
        if not req:
            return True
        eu = self.end_user or {}
        have = set(eu.get("entitlements") or []) | set(eu.get("roles") or [])
        return all(r in have for r in req)

    def sandbox_backend_for(self, config: dict) -> Any:
        """Deep-agent sandbox backend from a node's sandbox config. (Phase 3+.)"""
        return self.sandbox
