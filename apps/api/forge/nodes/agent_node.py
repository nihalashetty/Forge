"""Agent node (`create_agent`) and Deep Agent node (`create_deep_agent`).

Doc 2 §9. Both produce a compiled LangGraph graph used as a node inside the
workflow. The embedded agent does NOT carry its own checkpointer/store — the
top-level workflow graph owns durability, and LangGraph propagates it to subgraphs
at runtime (avoids nested-checkpointer conflicts and makes HITL interrupts bubble up).
"""

from __future__ import annotations

from typing import Any

from forge.engine.context import CompileContext
from forge.engine.middleware_compiler import build_middleware
from forge.engine.models import resolve_model
from forge.engine.registry import NodeSpec, Port, register


def _build_prompt(config: dict) -> str | None:
    # Static system prompt. Dynamic prompts compile to a middleware (added later).
    return config.get("system_prompt")


def _build_response_format(config: dict) -> Any:
    rf = config.get("response_format")
    if not rf or rf.get("mode") != "structured":
        return None
    # create_agent accepts a raw JSON-schema dict (auto provider/tool strategy).
    return rf.get("schema")


def build_subagents(subagents_cfg: list[dict], ctx: CompileContext) -> list[dict]:
    """Convert subagent configs to the dict shape SubAgentMiddleware expects.

    Inline subagents pass through (name/description/system_prompt/tools/model).
    `workflow_ref` subagents (CompiledSubAgent wrapping another workflow) are
    wired in the subworkflow phase; skipped here with their tools resolved.
    """
    out: list[dict] = []
    for sa in subagents_cfg or []:
        if "workflow_ref" in sa:
            continue  # TODO(phase: subworkflow): wrap compiled workflow as CompiledSubAgent
        spec: dict[str, Any] = {"name": sa["name"], "description": sa.get("description", "")}
        if sa.get("system_prompt"):
            spec["system_prompt"] = sa["system_prompt"]
        if sa.get("tools"):
            spec["tools"] = ctx.tools_for(sa["tools"])
        if sa.get("model"):
            spec["model"] = resolve_model(sa["model"], ctx)
        if sa.get("middleware"):
            spec["middleware"] = build_middleware(sa["middleware"], ctx)
        out.append(spec)
    return out


def _maybe_add_prompt_caching(stack: list[dict], config: dict, ctx: CompileContext) -> list[dict]:
    """Prepend Anthropic prompt-caching middleware for Anthropic-model agents (cost lever),
    unless already present or disabled. Best-effort: only when langchain-anthropic exists."""
    import importlib.util

    from forge.config import settings

    if not settings.default_anthropic_prompt_caching:
        return stack
    model_ref = config.get("model") or getattr(ctx, "default_model", "") or ""
    if not (isinstance(model_ref, str) and model_ref.startswith("anthropic")):
        return stack
    if any((m or {}).get("type") == "anthropic_prompt_caching" for m in stack):
        return stack
    if importlib.util.find_spec("langchain_anthropic") is None:
        return stack
    return [{"type": "anthropic_prompt_caching", "config": {}}, *stack]


def _common_kwargs(config: dict, ctx: CompileContext) -> dict:
    tools = list(ctx.tools_for(config.get("tools", [])))
    # Built-in knowledge access (RAG / Q&A) attached straight to the agent via its
    # `knowledge` config — no separate Tool row needed (see tools/builtin.py).
    if config.get("knowledge"):
        from forge.tools.builtin import build_knowledge_capability_tools
        tools += build_knowledge_capability_tools(config["knowledge"], ctx)
    # Agent-scoped MCP server access: attach each selected server's enabled tools
    # (pre-loaded by the runtime assembler into ctx.mcp_tools_by_client; native MCP tools).
    for cid in config.get("mcp_servers", []) or []:
        tools += (getattr(ctx, "mcp_tools_by_client", None) or {}).get(cid, [])
    stack = (ctx.project_default_mw or []) + (config.get("middleware") or [])
    stack = _maybe_add_prompt_caching(stack, config, ctx)
    middleware = build_middleware(stack, ctx)
    model = resolve_model(config.get("model"), ctx, config.get("model_params"))

    common: dict[str, Any] = {"model": model, "tools": tools, "middleware": middleware}
    prompt = _build_prompt(config)
    if prompt:
        common["system_prompt"] = prompt
    rf = _build_response_format(config)
    if rf is not None:
        common["response_format"] = rf
    if config.get("name"):
        common["name"] = config["name"]
    return common


def _resolve_config(config: dict, ctx: CompileContext) -> dict:
    """If the node mirrors a saved agent (`agent_ref`), the live preset drives it — so
    edits in the Agents tab take effect without re-saving the workflow. Falls back to the
    node's own (snapshot) config when the preset is missing/unresolved."""
    ref = config.get("agent_ref")
    if ref:
        preset = (getattr(ctx, "agent_presets", None) or {}).get(ref)
        if preset:
            return dict(preset)
    return config


def agent_factory(config: dict, ctx: CompileContext):
    config = _resolve_config(config, ctx)
    common = _common_kwargs(config, ctx)

    if config.get("flavor") == "deep_agent":
        try:
            from deepagents import create_deep_agent
        except ImportError as e:  # pragma: no cover - deepagents is a core dep
            raise ImportError(
                "deep_agent flavor needs `deepagents` (a core dependency — "
                "reinstall with `pip install -e .`)."
            ) from e
        subagents = build_subagents(config.get("subagents", []), ctx)
        kwargs: dict[str, Any] = dict(common)
        if subagents:
            kwargs["subagents"] = subagents
        backend = ctx.sandbox_backend_for(config.get("sandbox", {}) or {})
        if backend is not None:
            kwargs["backend"] = backend
        return create_deep_agent(**kwargs)

    from langchain.agents import create_agent

    return create_agent(**common)


def _summary(config: dict) -> list[str]:
    model = config.get("model", "—")
    n_tools = len(config.get("tools", []) or [])
    n_mw = len([m for m in (config.get("middleware") or []) if m.get("enabled", True)])
    flavor = config.get("flavor", "agent")
    line2 = f"{n_tools} tools · {n_mw} middleware"
    k = config.get("knowledge") or {}
    kbits = [name for name, key in (("RAG", "rag"), ("Q&A", "qa")) if (k.get(key) or {}).get("enabled")]
    if kbits:
        line2 += " · KB " + "+".join(kbits)
    n_mcp = len(config.get("mcp_servers", []) or [])
    if n_mcp:
        line2 += f" · MCP {n_mcp}"
    if flavor == "deep_agent":
        line2 += f" · subagents {len(config.get('subagents', []) or [])}"
    return [str(model), line2]


_ports = (
    [Port(id="in", io_type="messages", direction="in")],
    [Port(id="out", io_type="messages", direction="out")],
)

register(
    NodeSpec(
        type="agent",
        schema_id="forge/nodes/agent",
        input_ports=_ports[0],
        output_ports=_ports[1],
        factory=agent_factory,
        allows_cycle=True,
        category="agents",
        label="Agent",
        description="ReAct tool loop",
        summarize=_summary,
    )
)

register(
    NodeSpec(
        type="deep_agent",
        schema_id="forge/nodes/agent",
        input_ports=_ports[0],
        output_ports=_ports[1],
        factory=agent_factory,
        allows_cycle=True,
        category="agents",
        label="Deep Agent",
        description="Planning + subagents harness",
        summarize=_summary,
    )
)
