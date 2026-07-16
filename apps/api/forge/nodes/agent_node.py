"""Agent node (`create_agent`) and Deep Agent node (`create_deep_agent`).

Doc 2 §9. Both produce a compiled LangGraph graph used as a node inside the
workflow. The embedded agent does NOT carry its own checkpointer/store - the
top-level workflow graph owns durability, and LangGraph propagates it to subgraphs
at runtime (avoids nested-checkpointer conflicts and makes HITL interrupts bubble up).
"""

from __future__ import annotations

import logging
from typing import Any

from forge.engine.context import CompileContext
from forge.engine.middleware_compiler import build_middleware
from forge.engine.models import resolve_model
from forge.engine.registry import NodeSpec, Port, register

log = logging.getLogger("forge.agent")


def _dedup_tools_by_name(tools: list) -> list:
    """Bind each tool NAME to the model at most once. `resolve_tool_ids` already de-dups by id (a
    tool shared across several sets is ONE record → one id, sent once), but tool names are NOT
    unique per project and the final list mixes sources (tools + knowledge + MCP + components), so
    two entries can still collide by name - which providers reject (OpenAI errors on a duplicate
    function name). Keep the first occurrence; drop later name-collisions with a warning."""
    seen: set[str] = set()
    out: list = []
    for t in tools:
        name = getattr(t, "name", None)
        if name is not None and name in seen:
            log.warning("agent tool name %r appears more than once; keeping the first, dropping the rest", name)
            continue
        if name is not None:
            seen.add(name)
        out.append(t)
    return out

# Forge's default output style: every agent reply renders as GitHub-Flavored Markdown
# (Feature 1 - structured responses). It lives in the system prompt, so it costs ~nothing
# per turn (and is cached by the Anthropic prompt-caching middleware). Opt out with
# config output_style="plain"; auto-skipped for structured-output agents (they emit JSON).
OUTPUT_STYLE = (
    "Format every reply as GitHub-Flavored Markdown so it renders cleanly: short "
    "paragraphs; `##`/`###` headings for sections; `-` or numbered lists; GFM tables for "
    "comparisons or structured data; fenced code blocks with a language hint for code; and "
    "**bold** for key terms. Keep the structure minimal - only as much as the answer needs "
    "- and never output raw HTML."
)


# When UI components are attached, structured data should be shown via a component (table/
# card/form), NOT a markdown table - so this variant drops the "GFM tables for structured
# data" clause to avoid competing with the widgets (audit B1).
OUTPUT_STYLE_WITH_COMPONENTS = (
    "Format every reply as GitHub-Flavored Markdown so it renders cleanly: short paragraphs; "
    "`##`/`###` headings; `-` or numbered lists; fenced code blocks with a language hint; and "
    "**bold** for key terms. For structured data (tables, cards, forms), prefer the available "
    "UI components over a markdown table. Keep structure minimal and never output raw HTML."
)


# Steer the agent to RENDER a fitting component instead of restating its data as prose, and to
# POSITION it correctly: calling a component tool returns a placeholder marker that the agent
# copies into its reply where the widget belongs - so the component is interleaved with the text
# in its natural place (mid-answer, after a heading, at the end) rather than always pinned to the
# top (which is what happens if placement is left to tool-call order). The last sentence is
# load-bearing: it makes clear components only PRESENT data, so the agent keeps using its
# retrieval/other tools normally - without it, the component guidance was competing with
# knowledge/FAQ search and the agent skipped it (audit Priority B + the KB regression). Only
# appended when config["components"] is non-empty.
COMPONENT_STYLE = (
    "You have UI components available as tools (their names match the components). If a "
    "component fits the data you want to show (a table, card, form, …), you MUST call that "
    "component tool with the data as its props INSTEAD of writing the same data as prose or a "
    "markdown table. The tool returns a placeholder marker like [[forge:component:ID]]; copy that "
    "marker verbatim into your reply at the exact position where the component should appear. You "
    "control the order - write text before and after the marker so the component lands in its "
    "natural place in the answer (in the middle, after a heading, or at the end), exactly as it "
    "would read in a normal reply. Never restate the component's contents as text. This governs "
    "only how you PRESENT data - keep using your other tools (search the knowledge base, look up "
    "FAQs, call APIs) normally to GET the information you need."
)


def _build_prompt(config: dict) -> str | None:
    # Static system prompt + Forge's default Markdown output style (+ component guidance when
    # components are attached). Dynamic prompts compile to a middleware (added later).
    base = (config.get("system_prompt") or "").strip()
    structured = (config.get("response_format") or {}).get("mode") == "structured"
    if structured or config.get("output_style") == "plain":
        return base or None
    has_components = bool(config.get("components"))
    style = OUTPUT_STYLE_WITH_COMPONENTS if has_components else OUTPUT_STYLE
    parts = ([base] if base else []) + [style]
    if has_components:
        parts.append(COMPONENT_STYLE)
    return "\n\n".join(parts).strip()


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
        if sa.get("tools") or sa.get("toolsets"):
            spec["tools"] = _dedup_tools_by_name(ctx.tools_for(ctx.resolve_tool_ids(sa.get("tools"), sa.get("toolsets"))))
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


def _clamp(value, n: int = 300):
    """Bound an end_user value's size before it enters the prompt (avoid bloat/abuse)."""
    if isinstance(value, str):
        return value[:n]
    if isinstance(value, list):
        return [_clamp(v, n) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k)[:60]: _clamp(v, n) for k, v in list(value.items())[:20]}
    return value


def _end_user_block(end_user: dict) -> str:
    """A generic identity-awareness block. Only a whitelisted, size-clamped subset of the
    (untrusted-shaped) end_user is embedded, re-serialized so the JSON stays well-formed
    (audit L4). The withhold-restriction sentence is added ONLY when the user actually carries
    roles/entitlements - an unscoped prohibition with no entitlement list made the model
    over-refuse general KB/FAQ answers ("I don't have that information") (audit Priority A)."""
    import json as _json

    safe = {
        k: _clamp(end_user[k])
        for k in ("id", "display_name", "email", "roles", "entitlements", "attributes")
        if end_user.get(k) not in (None, "", [], {})
    }
    if not safe:
        return ""
    eu = _json.dumps(safe, default=str, ensure_ascii=False)
    line = (
        "[END USER] You are assisting this authenticated end user, provided by the host "
        f"application - treat it as authoritative: {eu}."
    )
    if safe.get("roles") or safe.get("entitlements"):
        line += (
            " General product, FAQ, and knowledge-base information is available to everyone - "
            "always answer it. Only withhold data that is specific to OTHER users or accounts "
            "this user is not entitled to see or act on."
        )
    return line


def _dynamic_field_middleware(config: dict, ctx: CompileContext, base_prompt: str | None) -> list:
    """Compile the agent node's `dynamic_model` / `dynamic_prompt` blocks - both exposed in the
    UI but previously unwired (audit F9) - into middleware.

    - dynamic_model reuses the proven `dynamic_model_by_state` builder (switch model by a
      state expression).
    - dynamic_prompt renders the FIRST matching rule's prompt (with `{{state.*}}` tokens) as
      the system prompt per model call, falling back to the node's static prompt when no rule
      matches - so enabling it with no matching rule is behavior-neutral."""
    extra: list = []

    dm = config.get("dynamic_model") or {}
    if dm.get("enabled") and dm.get("rules"):
        extra += build_middleware(
            [{"type": "dynamic_model_by_state", "config": {"rules": dm["rules"], "default": dm.get("default")}}],
            ctx,
        )

    dp = config.get("dynamic_prompt") or {}
    rules = dp.get("rules") or []
    if dp.get("enabled") and rules:
        from langchain.agents.middleware import dynamic_prompt as _dynamic_prompt

        from forge.auth_providers.templates import render_template
        from forge.engine.expressions import ExpressionError, eval_truthy

        fallback = base_prompt or ""

        @_dynamic_prompt
        def _prompt(request):  # type: ignore[no-untyped-def]
            state = dict(getattr(request, "state", {}) or {})
            for r in rules:
                text = r.get("prompt")
                if not text:
                    continue
                when = r.get("when")
                try:
                    if not when or eval_truthy(when, state):
                        rendered = render_template(text, {"state": state}) if isinstance(text, str) else text
                        return str(rendered) if rendered is not None else fallback
                except ExpressionError:
                    continue
            return fallback

        extra.append(_prompt)

    return extra


def _common_kwargs(config: dict, ctx: CompileContext) -> dict:
    tools = list(ctx.tools_for(ctx.resolve_tool_ids(config.get("tools"), config.get("toolsets"))))
    # Built-in knowledge access (RAG / Q&A) attached straight to the agent via its
    # `knowledge` config - no separate Tool row needed (see tools/builtin.py).
    if config.get("knowledge"):
        from forge.tools.builtin import build_knowledge_capability_tools
        tools += build_knowledge_capability_tools(config["knowledge"], ctx)
    # Agent-scoped MCP server access: attach each selected server's enabled tools
    # (pre-loaded by the runtime assembler into ctx.mcp_tools_by_client; native MCP tools).
    for cid in config.get("mcp_servers", []) or []:
        tools += (getattr(ctx, "mcp_tools_by_client", None) or {}).get(cid, [])
    # User-defined UI components exposed as widget-tools (Feature 2): the agent "renders"
    # one by calling it; the client draws the saved template from the props it passes.
    tools += list(ctx.components_for(config.get("components", [])))
    # Final guard: exactly one function name per model call, whatever the source mix.
    tools = _dedup_tools_by_name(tools)
    stack = (ctx.project_default_mw or []) + (config.get("middleware") or [])
    stack = _maybe_add_prompt_caching(stack, config, ctx)
    middleware = build_middleware(stack, ctx)
    model = resolve_model(config.get("model"), ctx, config.get("model_params"))

    common: dict[str, Any] = {"model": model, "tools": tools, "middleware": middleware}
    prompt = _build_prompt(config)
    # Identity awareness: if the run acts for an end user, append a generic context block so
    # the agent knows who it's helping and to stay within their entitlements. Appended last,
    # so the (cacheable) instructions prefix is unchanged; only this per-user suffix varies.
    end_user = getattr(ctx, "end_user", None)
    if end_user:
        eu_block = _end_user_block(end_user)
        if eu_block:
            prompt = f"{prompt}\n\n{eu_block}" if prompt else eu_block
    if prompt:
        common["system_prompt"] = prompt
    # Wire the dynamic_model / dynamic_prompt config blocks (append after the static stack so
    # a matching rule overrides the base at call time). base_prompt = the fully-built static
    # prompt so a non-matching dynamic_prompt run reproduces the static behavior exactly.
    dynamic_mw = _dynamic_field_middleware(config, ctx, prompt)
    if dynamic_mw:
        common["middleware"] = list(middleware) + dynamic_mw
    rf = _build_response_format(config)
    if rf is not None:
        common["response_format"] = rf
    if config.get("name"):
        common["name"] = config["name"]
    return common


def _resolve_config(config: dict, ctx: CompileContext) -> dict:
    """If the node mirrors a saved agent (`agent_ref`), the live preset drives it - so
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
                "deep_agent flavor needs `deepagents` (a core dependency - "
                "reinstall with `pip install -e .`)."
            ) from e
        subagents = build_subagents(config.get("subagents", []), ctx)
        kwargs: dict[str, Any] = dict(common)
        if subagents:
            kwargs["subagents"] = subagents
        backend = ctx.sandbox_backend_for(config.get("sandbox", {}) or {})
        if backend is not None:
            kwargs["backend"] = backend
        # Deep-agent skills (agent-skills source paths) map straight through to create_deep_agent
        # (audit F9). filesystem/permissions/memory are intentionally NOT auto-wired here - their
        # UI shapes don't map 1:1 to the deepagents API and mis-wiring a filesystem permission
        # would be a security footgun, so validate_workflow WARNS on them instead (see
        # services/validation._warn_unwired_agent_fields).
        # TODO(F9): map `permissions` ({path,access}) -> FilesystemPermission and `filesystem`
        # ({kind,routes}) -> a deepagents backend once the UI schema is reconciled.
        if config.get("skills"):
            kwargs["skills"] = list(config["skills"])
        return create_deep_agent(**kwargs)

    from langchain.agents import create_agent

    return create_agent(**common)


def _summary(config: dict) -> list[str]:
    model = config.get("model", "-")
    n_tools = len(config.get("tools", []) or [])
    n_mw = len([m for m in (config.get("middleware") or []) if m.get("enabled", True)])
    flavor = config.get("flavor", "agent")
    line2 = f"{n_tools} tools · {n_mw} middleware"
    n_comp = len(config.get("components", []) or [])
    if n_comp:
        line2 += f" · {n_comp} widget{'s' if n_comp != 1 else ''}"
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
