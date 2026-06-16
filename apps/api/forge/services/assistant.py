"""Forge Assistant — a meta-agent whose tools are the Forge platform itself.

It can inspect a project and *build* into it: create tools, auth providers, Q&A,
knowledge, and workflows; and explain how the pieces fit together. It runs with the
project's own provider key (resolved from the secret store) and streams its narration
token-by-token over SSE, emitting `tool` events as it performs each action.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import select

from forge.db.base import SessionLocal
from forge.engine.models import default_model_for_credentials, resolve_model
from forge.models import Agent, AuthProvider, KbSource, Project, QaPair, Tool, Workflow
from forge.services.knowledge import KnowledgeService
from forge.services.runtime import build_compile_context
from forge.services.workflows import WorkflowService
from forge.util.serialize import content_to_text, jsonable

GROUNDING_PROMPT = (
    "You are the support assistant for this project. Be friendly, natural, and concise.\n"
    "- For greetings, thanks, goodbyes, or small talk (e.g. 'hi', 'thanks'), reply naturally and "
    "briefly and invite the user's question. Do NOT refuse these.\n"
    "- For questions about this project/product/company, answer using ONLY the KNOWLEDGE BASE "
    "context provided in the conversation (documents and FAQs). If that context does not contain "
    "the answer, say you don't have that information and offer to connect them with a human. Never "
    "invent facts or rely on outside knowledge for such questions.\n"
    "- You may use the prior conversation turns for context (e.g. follow-up questions)."
)

AUTH_TEMPLATES: dict[str, dict] = {
    "bearer": {"kind": "bearer", "token_ref": "secret://proj/token", "header_name": "Authorization", "prefix": "Bearer "},
    "api_key": {"kind": "api_key", "in": "header", "name": "X-API-Key", "value_ref": "secret://proj/api_key"},
    "basic": {"kind": "basic", "username_ref": "secret://proj/user", "password_ref": "secret://proj/pass"},
    "oauth2_client_credentials": {
        "kind": "oauth2_client_credentials", "token_url": "https://idp.example.com/oauth/token",
        "scope": "read", "client_id_ref": "secret://proj/client_id", "client_secret_ref": "secret://proj/client_secret",
    },
    "csrf_session": {
        "kind": "csrf_session", "credentials_ref": "secret://proj/creds",
        "token_fetch": {"method": "POST", "url": "https://app.example.com/login", "headers": {"Content-Type": "application/json"}, "body": {"username": "{{cred.username}}", "password": "{{cred.password}}"}},
        "extract": [{"name": "csrf", "from": "header", "header": "X-CSRF-Token"}],
        "inject": [{"to": "header", "name": "X-CSRF-Token", "value": "{{extracted.csrf}}"}],
        "cache_ttl_seconds": 1800, "refresh_on": [401, 403],
    },
}


def _node(nid: str, ntype: str, cfg: dict, x: int, y: int) -> dict:
    return {"id": nid, "type": ntype, "config": cfg, "position": {"x": x, "y": y}}


def _warn_note(warnings: list | None) -> str:
    if not warnings:
        return ""
    msgs = "; ".join(w.get("message", "") for w in warnings[:3])
    return f" VALIDATION WARNINGS (fix if unintended): {msgs}"


def _canvas(nodes: list[dict], edges: list[dict]) -> dict:
    # The frontend (canvasToFlow) reads node config from data.config and the node kind
    # from data.nodeType. Emitting the bare config as `data` (the old bug) made the
    # builder show every node as empty/unconfigured even though the executable was fine.
    by_id = {n["id"]: n for n in nodes}

    def source_handle(edge: dict) -> str | None:
        if edge.get("sourceHandle") or edge.get("source_handle"):
            return edge.get("sourceHandle") or edge.get("source_handle")
        source = by_id.get(edge["source"])
        if not source or source.get("type") != "router":
            return None
        cfg = source.get("config") or {}
        cases = cfg.get("cases") or {}
        label = edge.get("label")
        if label is not None and str(label) in cases:
            return f"case:{label}"
        for key, target in cases.items():
            if target == edge.get("target"):
                return f"case:{key}"
        if cfg.get("default") == edge.get("target"):
            return "case:__default__"
        return None

    return {
        "nodes": [
            {"id": n["id"], "type": "forge", "position": n["position"],
             "data": {"nodeType": n["type"], "config": n["config"]}}
            for n in nodes
        ],
        "edges": [
            {"id": f"e{i}", "source": e["source"], "target": e["target"],
             **({"sourceHandle": source_handle(e)} if source_handle(e) else {}),
             **({"label": str(e["label"])} if e.get("label") is not None else {})}
            for i, e in enumerate(edges)
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


async def _default_agent_model(session, tenant_id: str, project_id: str) -> str:
    """Pick a real model ref for created agents, based on the project's configured keys."""
    proj = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    cfg = (proj.config or {}) if proj else {}
    explicit = cfg.get("default_model")
    if explicit and not str(explicit).startswith("fake"):
        return explicit
    return (
        default_model_for_credentials(cfg.get("provider_credentials"))
        or explicit
        or "fake:Configure a provider key for live answers."
    )


def build_assistant_tools(tenant_id: str, project_id: str, mutated: list) -> list:
    """StructuredTools bound to one project. `mutated` collects created-resource notes."""
    from langchain_core.tools import StructuredTool

    async def list_resources() -> str:
        """List everything in this project: tools, workflows, agents, auth providers, knowledge sources, and Q&A pairs. Use this to answer 'what do I have' and before deciding what to build."""
        async with SessionLocal() as s:
            def names(rows, attr="name"):
                return [getattr(r, attr) for r in rows] or ["(none)"]
            tools = (await s.execute(select(Tool).where(Tool.project_id == project_id))).scalars().all()
            wfs = (await s.execute(select(Workflow).where(Workflow.project_id == project_id))).scalars().all()
            agents = (await s.execute(select(Agent).where(Agent.project_id == project_id))).scalars().all()
            aps = (await s.execute(select(AuthProvider).where(AuthProvider.project_id == project_id))).scalars().all()
            srcs = (await s.execute(select(KbSource).where(KbSource.project_id == project_id))).scalars().all()
            qas = (await s.execute(select(QaPair).where(QaPair.project_id == project_id))).scalars().all()
            return json.dumps({
                "tools": [f"{t.name} ({t.kind})" for t in tools] or ["(none)"],
                "workflows": [f"{w.name} [{w.status}]" for w in wfs] or ["(none)"],
                "agents": names(agents), "auth_providers": [f"{a.name} ({a.kind})" for a in aps] or ["(none)"],
                "knowledge_sources": names(srcs), "qa_pairs": len(qas),
            })

    async def describe_workflow(name_or_id: str = "") -> str:
        """Describe a workflow's structure (its nodes and how they connect) so you can explain how it works. Pass a name or id; empty picks the active workflow."""
        async with SessionLocal() as s:
            wfs = (await s.execute(select(Workflow).where(Workflow.project_id == project_id))).scalars().all()
            wf = None
            if name_or_id:
                wf = next((w for w in wfs if w.id == name_or_id or w.name.lower() == name_or_id.lower()), None)
            wf = wf or next((w for w in wfs if w.status == "active"), None) or (wfs[0] if wfs else None)
            if not wf:
                return "No workflows exist yet."
            ex = wf.executable or {}
            nodes = [{"id": n["id"], "type": n["type"], "config": {k: v for k, v in (n.get("config") or {}).items() if k in ("model", "name", "top_k", "expression")}} for n in ex.get("nodes", [])]
            edges = [f"{e['source']} → {e['target']}" for e in ex.get("edges", [])]
            return json.dumps({"name": wf.name, "status": wf.status, "nodes": nodes, "flow": edges})

    async def create_agent_preset(name: str, instructions: str, model: str = "", tool_names: str = "") -> str:
        """Create OR UPDATE (idempotent by name) a reusable agent preset with the given system
        `instructions`. `model` like 'openai:gpt-4o-mini' (empty = project default). `tool_names`
        is an optional comma-separated list of EXISTING project tool names to attach. The preset
        appears in the Agents screen and can be dropped into workflows."""
        from forge.services.agents import AgentService
        async with SessionLocal() as s:
            mdl = model or await _default_agent_model(s, tenant_id, project_id)
            wanted = [t.strip() for t in tool_names.split(",") if t.strip()]
            tool_ids: list[str] = []
            missing: list[str] = []
            if wanted:
                rows = (await s.execute(select(Tool).where(Tool.project_id == project_id))).scalars().all()
                by_name = {t.name: t.id for t in rows}
                for w in wanted:
                    (tool_ids.append(by_name[w]) if w in by_name else missing.append(w))
            cfg = {"flavor": "agent", "model": mdl, "system_prompt": instructions, "tools": tool_ids, "middleware": []}
            existing = [a for a in await AgentService.list(s, tenant_id, project_id) if a.name == name]
            if existing:
                agent = await AgentService.update(s, existing[0], config=cfg)
                verb = "Updated existing"
            else:
                agent = await AgentService.create(s, tenant_id, project_id, name=name.strip().replace(" ", "_"), config=cfg)
                verb = "Created"
        mutated.append({"kind": "agent", "name": agent.name})
        note = f" (unknown tools skipped: {', '.join(missing)})" if missing else ""
        return f"{verb} agent preset '{agent.name}' on {mdl} with {len(tool_ids)} tool(s){note}. See the Agents screen."

    async def create_builtin_tool(name: str, builtin: str, description: str = "") -> str:
        """Create a builtin tool. `builtin` is one of: current_time, calculator, web_fetch,
        web_search, knowledge_search. knowledge_search lets an AGENT search the project
        knowledge base itself (docs + FAQs, optional folder filter) — attach it to one agent
        instead of a classifier→router when a question may have several parts."""
        from forge.services.tools import ToolService
        if builtin not in ("current_time", "calculator", "web_fetch", "web_search", "knowledge_search", "remember", "recall"):
            return f"ERROR: unknown builtin '{builtin}'."
        async with SessionLocal() as s:
            t = await ToolService.create(s, tenant_id, project_id, name=name.strip().replace(" ", "_"), kind="builtin",
                                         config={"builtin": builtin, "description": description or f"{builtin} builtin"})
        mutated.append({"kind": "tool", "name": t.name})
        return f"Created builtin tool '{t.name}' (id {t.id})."

    async def create_rest_tool(name: str, url_template: str, method: str = "GET", description: str = "", projection_jmespath: str = "") -> str:
        """Create a REST API tool. url_template may contain {placeholders} that become required path params. projection_jmespath (optional) trims the response before it reaches the model."""
        import re

        from forge.services.tools import ToolService
        params = re.findall(r"\{(\w+)\}", url_template)
        fields = [{"path": p, "type": "string", "in": "path", "required": True, "llm_visible": True, "description": p} for p in params]
        cfg: dict[str, Any] = {
            "description": description or f"{method} {url_template}",
            "request": {"method": method.upper(), "url_template": url_template, "fields": fields, "headers": [{"name": "Accept", "value": "application/json"}]},
            "response": {"projection_jmespath": projection_jmespath} if projection_jmespath else {},
            "timeout_seconds": 30,
        }
        async with SessionLocal() as s:
            t = await ToolService.create(s, tenant_id, project_id, name=name.strip().replace(" ", "_"), kind="rest_api", config=cfg)
        mutated.append({"kind": "tool", "name": t.name})
        return f"Created REST tool '{t.name}' ({method.upper()} {url_template}) with {len(fields)} path param(s)."

    async def create_auth_provider(name: str, kind: str = "bearer") -> str:
        """Create an auth provider from a template. kind: bearer, api_key, basic, oauth2_client_credentials, or csrf_session. The user can fill in secrets afterward."""
        from forge.services.auth_providers import AuthProviderService
        cfg = AUTH_TEMPLATES.get(kind)
        if not cfg:
            return f"ERROR: unknown auth kind '{kind}'. Use one of: {', '.join(AUTH_TEMPLATES)}."
        async with SessionLocal() as s:
            ap = await AuthProviderService.create(s, tenant_id, project_id, name=name.strip().replace(" ", "_"), kind=kind, config=dict(cfg), credentials_ref=cfg.get("credentials_ref") or cfg.get("token_ref") or cfg.get("value_ref"))
        mutated.append({"kind": "auth_provider", "name": ap.name})
        return f"Created '{kind}' auth provider '{ap.name}'. Open Auth Providers to add the secret values."

    async def add_qa_pair(question: str, answer: str, kind: str = "faq") -> str:
        """Add a Q&A pair to the knowledge base so the workflow can answer this question with
        this exact answer. `kind` is a free-form category — 'faq', 'error_workaround', or any
        custom kind the user wants (retrieval nodes and agent Q&A can filter by it)."""
        async with SessionLocal() as s:
            qa = await KnowledgeService.create_qa(s, tenant_id, project_id, question=question, answer=answer, kind=kind or "faq")
        mutated.append({"kind": "qa", "name": question[:40]})
        return f"Added Q&A pair ({qa.kind}): '{question[:60]}' (id {qa.id})."

    async def add_knowledge_text(name: str, text: str, folder: str = "") -> str:
        """Add a document of text to the knowledge base (it is split, embedded, and made
        searchable for grounding). `folder` (optional, free-form) organizes sources —
        retrieval nodes and knowledge_search tools can filter by folder."""
        async with SessionLocal() as s:
            src = await KnowledgeService.create_source(s, tenant_id, project_id, kind="text", name=name, text=text, folder=folder)
            src = await KnowledgeService.ingest(s, src)
        mutated.append({"kind": "knowledge", "name": name})
        if src.status != "ready":
            return f"ERROR ingesting '{name}': {(src.meta or {}).get('error', 'unknown')}."
        where = f" in folder '{folder}'" if folder else ""
        return f"Added knowledge source '{name}'{where} ({src.chunks} chunk(s), embedded)."

    def _auto_declare_state(nodes: list, state: dict) -> dict:
        """Declare the state keys nodes write (mirrors the canvas's nodeWrittenKeys) so
        generated definitions don't fail on undeclared-key writes."""
        out = dict(state or {})
        for n in nodes:
            cfg = n.get("config") or {}
            t = n.get("type")
            written: list[tuple[str, str]] = []
            if t == "classifier":
                written.append((cfg.get("output_key", "intent"), "list[str]" if cfg.get("multi_label") else "str"))
            elif t == "retrieval" and cfg.get("route_key"):
                written.append((cfg["route_key"], "str"))
            elif t == "human_input" and cfg.get("output_key"):
                written.append((cfg["output_key"], "str"))
            elif t == "transform":
                written.append((cfg.get("output_key", "data"), "json"))
            elif t in ("tool_call", "webhook_out") and cfg.get("output_key"):
                written.append((cfg["output_key"], "json"))
            for key, typ in written:
                if key and key not in out:
                    out[key] = {"type": typ, "reducer": "last"}
        return out

    async def _save_workflow(
        s, *, name: str, description: str, nodes: list, edges: list, state: dict,
        entry_node: str = "start",
    ) -> tuple[str, str, list]:
        """Validate + idempotently create/update a workflow by exact name.
        Returns (verb, error, warnings)."""
        ex = {"id": "wf", "version": 1, "state": _auto_declare_state(nodes, state), "entry_node": entry_node,
              "global_middleware": [{"type": "model_call_limit", "config": {"run_limit": 25}}],
              "nodes": nodes, "edges": edges}
        result = WorkflowService.validate(ex)
        if not result.valid:
            details = "; ".join(f"{e.get('pointer', '')}: {e.get('message', '')}" for e in result.errors)
            return "", "generated workflow failed validation: " + details, result.warnings
        existing = [w for w in await WorkflowService.list(s, tenant_id, project_id) if w.name == name]
        if existing:
            wf = existing[0]
            await WorkflowService.save_canvas(s, wf, _canvas(nodes, edges), ex)
            wf.status = "active"
            await s.commit()
            verb = "Updated existing"
        else:
            wf = await WorkflowService.create(s, tenant_id, project_id, name=name, description=description)
            await WorkflowService.save_canvas(s, wf, _canvas(nodes, edges), ex)
            wf.status = "active"
            await s.commit()
            verb = "Created and activated"
        mutated.append({"kind": "workflow", "name": name})
        return verb, "", result.warnings

    _CHAT_STATE = {"messages": {"type": "list[message]", "reducer": "add_messages"}, "intent": {"type": "str", "reducer": "last"}}

    async def create_grounded_workflow(name: str = "Grounded support", instructions: str = "", model: str = "",
                                       review_before_reply: bool = False, approve_tools: str = "") -> str:
        """Create OR UPDATE a grounded support workflow: start → retrieval → grounded agent → end.
        The retrieval node pulls BOTH the project's knowledge-base docs AND its Q&A pairs into
        context (include_qa), and the agent answers strictly from that context. Idempotent by name.
        `instructions` sets the agent's system prompt; `model` overrides the model.
        HUMAN-IN-THE-LOOP (real interrupts, not prompt text):
        - review_before_reply=True inserts a human_input node before end — the run PAUSES and a
          human must approve/reject the agent's draft before it is final.
        - approve_tools='tool_a, tool_b' attaches HumanInTheLoopMiddleware so those tool calls
          pause for approval before executing.
        Call list_resources first so you reuse a name instead of duplicating."""
        async with SessionLocal() as s:
            mdl = model or await _default_agent_model(s, tenant_id, project_id)
            agent_cfg: dict[str, Any] = {"flavor": "agent", "name": "support_agent", "model": mdl, "system_prompt": instructions or GROUNDING_PROMPT}
            # Default summarization so long conversations don't grow cost every turn
            # (triggers at ~6k tokens, keeps the last 6 messages). Users can remove it.
            agent_cfg["middleware"] = [{"type": "summarization", "enabled": True,
                                        "config": {"trigger": ["tokens", 6000], "keep": ["messages", 6]}}]
            tool_names = [t.strip() for t in approve_tools.split(",") if t.strip()]
            if tool_names:
                agent_cfg["middleware"].append({"type": "human_in_the_loop", "enabled": True,
                                                "config": {"interrupt_on": {t: True for t in tool_names}}})
            nodes = [
                _node("start", "start", {}, 40, 180),
                _node("retrieval_1", "retrieval", {"top_k": 4, "include_qa": True, "announce_empty": True, "min_score": 0.18}, 300, 180),
                _node("agent_1", "agent", agent_cfg, 560, 180),
            ]
            edges = [{"source": "start", "target": "retrieval_1"}, {"source": "retrieval_1", "target": "agent_1"}]
            if review_before_reply:
                nodes.append(_node("review_1", "human_input", {"prompt": "Review the agent's draft reply above and approve or reject it.", "allowed_decisions": ["approve", "reject"]}, 820, 180))
                nodes.append(_node("end", "end", {}, 1080, 180))
                edges += [{"source": "agent_1", "target": "review_1"}, {"source": "review_1", "target": "end"}]
            else:
                nodes.append(_node("end", "end", {}, 840, 180))
                edges += [{"source": "agent_1", "target": "end"}]
            verb, err, warns = await _save_workflow(s, name=name, description="Retrieval-grounded support agent over the project knowledge base + FAQs.", nodes=nodes, edges=edges, state=_CHAT_STATE)
        if err:
            return "ERROR: " + err
        hitl_note = " Includes a human review pause before the final reply." if review_before_reply else (
            f" Tool calls requiring approval: {', '.join(tool_names)}." if tool_names else "")
        return f"{verb} grounded workflow '{name}': start → retrieval (docs + Q&A) → agent → {'review → ' if review_before_reply else ''}end.{hitl_note}{_warn_note(warns)} Test it in the Playground."

    async def create_intent_router_workflow(name: str = "Intent router", intents: str = "", instructions: str = "", model: str = "") -> str:
        """Create OR UPDATE an intent-routing workflow (the classify-then-route pattern):
        start → classifier (structured output picks ONE intent label) → router (one case per
        intent) → a specialist agent per intent → end, plus a general agent for everything else.
        `intents` is a comma-separated list of labels, e.g. 'return_item, cancel_subscription,
        get_information'. `instructions` is appended to every specialist's prompt. Idempotent by
        name. Use this when the user wants different handling per request type."""
        labels = [i.strip().replace(" ", "_") for i in intents.split(",") if i.strip()]
        if len(labels) < 2:
            return "ERROR: pass at least 2 comma-separated intents, e.g. intents='return_item, cancel_subscription'."
        async with SessionLocal() as s:
            mdl = model or await _default_agent_model(s, tenant_id, project_id)
            base = instructions or "Be concise and helpful."
            nodes = [
                _node("start", "start", {}, 40, 220),
                _node("classify", "classifier", {"labels": labels, "output_key": "intent", "model": mdl}, 260, 220),
                _node("route", "router", {"expression": "intent", "cases": {label: f"{label}_agent" for label in labels}, "default": "general_agent"}, 520, 220),
            ]
            edges = [{"source": "start", "target": "classify"}, {"source": "classify", "target": "route"}]
            for i, label in enumerate(labels):
                agent_id = f"{label}_agent"
                nodes.append(_node(agent_id, "agent", {
                    "flavor": "agent", "name": agent_id, "model": mdl,
                    "system_prompt": f"You are the specialist for '{label.replace('_', ' ')}' requests. {base}",
                }, 800, 80 + i * 140))
                edges.append({"source": "route", "target": agent_id, "label": label})
                edges.append({"source": agent_id, "target": "end"})
            nodes.append(_node("general_agent", "agent", {
                "flavor": "agent", "name": "general_agent", "model": mdl,
                "system_prompt": f"You handle anything that doesn't fit a known category. {base}",
            }, 800, 80 + len(labels) * 140))
            edges.append({"source": "route", "target": "general_agent", "label": "else"})
            edges.append({"source": "general_agent", "target": "end"})
            nodes.append(_node("end", "end", {}, 1080, 220))
            verb, err, warns = await _save_workflow(s, name=name, description=f"Classifies into {', '.join(labels)} and routes to a specialist agent per intent.", nodes=nodes, edges=edges, state=_CHAT_STATE)
        if err:
            return "ERROR: " + err
        return f"{verb} intent-router workflow '{name}': classifier → router → one specialist per intent ({', '.join(labels)}) + a general fallback.{_warn_note(warns)} Test each intent in the Playground."

    async def add_human_review(workflow_name_or_id: str, prompt: str = "Review the draft reply and approve or reject.") -> str:
        """Insert a REAL human-approval pause (a human_input interrupt node) into an EXISTING
        workflow, right before it ends. The run will PAUSE in the Playground until a person
        approves or rejects. Use this whenever the user wants HITL/approval added to a workflow
        that already exists — never simulate approval through agent instructions. Idempotent:
        if the workflow already has a human_input node, only its prompt is updated."""
        async with SessionLocal() as s:
            wfs = await WorkflowService.list(s, tenant_id, project_id)
            wf = next((w for w in wfs if w.id == workflow_name_or_id), None) or next(
                (w for w in wfs if w.name.lower() == workflow_name_or_id.lower()), None)
            if not wf:
                return f"No workflow named or id '{workflow_name_or_id}'. Use list_resources first."
            ex = dict(wf.executable or {})
            nodes = [dict(n) for n in ex.get("nodes", [])]
            edges = [dict(e) for e in ex.get("edges", [])]
            existing = next((n for n in nodes if n["type"] == "human_input"), None)
            if existing:
                existing["config"] = {**(existing.get("config") or {}), "prompt": prompt}
            else:
                end_ids = {n["id"] for n in nodes if n["type"] == "end"}
                if not end_ids:
                    return f"Workflow '{wf.name}' has no end node — open it on the canvas first."
                first_end = next(n for n in nodes if n["type"] == "end")
                gate = _node("human_review", "human_input",
                             {"prompt": prompt, "allowed_decisions": ["approve", "reject"]},
                             max(0, first_end["position"]["x"] - 240), first_end["position"]["y"] + 120)
                for e in edges:
                    if e.get("target") in end_ids:
                        e["target"] = "human_review"
                for n in nodes:
                    if n["type"] == "router":
                        cfg = n.get("config") or {}
                        cfg["cases"] = {k: ("human_review" if v in end_ids else v) for k, v in (cfg.get("cases") or {}).items()}
                        if cfg.get("default") in end_ids:
                            cfg["default"] = "human_review"
                        n["config"] = cfg
                nodes.append(gate)
                edges.append({"source": "human_review", "target": first_end["id"]})
            verb, err, warns = await _save_workflow(s, name=wf.name, description=wf.description or "", nodes=nodes, edges=edges, state=ex.get("state") or _CHAT_STATE, entry_node=ex.get("entry_node") or "start")
        if err:
            return "ERROR: " + err
        return f"Added a human-review pause to '{wf.name}': every reply now stops at an approval gate before finishing.{_warn_note(warns)} Test it in the Playground — the run will pause with approve/reject buttons."

    async def test_workflow(name_or_id: str, message: str) -> str:
        """Run a workflow with a test message and return its final answer plus the nodes it
        visited. ALWAYS use this after building or changing a workflow to VERIFY it behaves
        correctly (e.g. test a greeting, an FAQ question, and an off-topic question). If the
        result is wrong, fix the workflow and test again until it is correct."""
        import uuid as _uuid

        from langgraph.checkpoint.memory import InMemorySaver

        from forge.engine.compiler import compile_workflow

        async with SessionLocal() as s:
            wfs = await WorkflowService.list(s, tenant_id, project_id)
            wf = next((w for w in wfs if w.id == name_or_id), None) or next(
                (w for w in wfs if w.name.lower() == name_or_id.lower()), None)
            if not wf:
                return f"No workflow named or id '{name_or_id}'. Use list_resources first."
            ctx = await build_compile_context(
                s, tenant_id=tenant_id, project_id=project_id, checkpointer=InMemorySaver()
            )
            try:
                graph = compile_workflow(wf.executable or {}, ctx)
            except Exception as e:  # noqa: BLE001 - surface compile failures to the agent
                return f"COMPILE ERROR in '{wf.name}': {type(e).__name__}: {e}"
        visited: list[str] = []
        final = ""
        interrupted = False
        interrupt_payloads: list = []
        config = {"configurable": {"thread_id": f"assistant-test-{_uuid.uuid4()}"}, "recursion_limit": 50}
        try:
            async for ns, mode, chunk in graph.astream(
                {"messages": [{"role": "user", "content": message}]},
                config,
                stream_mode=["updates", "values"], subgraphs=True,
            ):
                if mode == "updates" and not ns and isinstance(chunk, dict):
                    visited.extend(k for k in chunk.keys() if k not in visited and k != "__interrupt__")
                    if "__interrupt__" in chunk:
                        interrupted = True
                        interrupt_payloads.append(jsonable(chunk["__interrupt__"]))
                        if "__interrupt__" not in visited:
                            visited.append("__interrupt__")
                if mode == "values" and isinstance(chunk, dict):
                    for m in chunk.get("messages", []):
                        if getattr(m, "type", "") == "ai" and getattr(m, "content", ""):
                            final = content_to_text(m.content)
            snapshot = await graph.aget_state(config)
            task_interrupts = [
                jsonable(getattr(t, "interrupts", None))
                for t in getattr(snapshot, "tasks", [])
                if getattr(t, "interrupts", None)
            ]
            if task_interrupts:
                interrupted = True
                interrupt_payloads.extend(task_interrupts)
        except Exception as e:  # noqa: BLE001
            return f"RUNTIME ERROR in '{wf.name}': {type(e).__name__}: {e}"
        return json.dumps({
            "workflow": wf.name,
            "nodes_visited": visited,
            "answer": final[:600] or "(no answer produced)",
            "interrupted": interrupted,
            "interrupts": interrupt_payloads,
        })

    async def list_node_types() -> str:
        """The LIVE catalog of workflow node types (type, label, category, description,
        config keys). Read this before composing a custom workflow so you only use node
        types and config fields that actually exist."""
        from forge.engine.registry import all_specs
        from forge.schemas import contracts

        out = []
        for spec in all_specs():
            schema = contracts.raw_schema(spec.schema_id) or {}
            out.append({
                "type": spec.type, "label": spec.label, "category": spec.category,
                "description": spec.description,
                "config_keys": sorted((schema.get("properties") or {}).keys()),
                "required": schema.get("required", []),
            })
        return json.dumps(out)

    async def get_node_schema(node_type: str) -> str:
        """Full JSON config schema for one node type — field types, defaults, and help
        text. Use when you need exact config details for a custom workflow."""
        from forge.engine.registry import NODE_REGISTRY
        from forge.schemas import contracts

        spec = NODE_REGISTRY.get(node_type)
        if not spec:
            return f"ERROR: unknown node type '{node_type}'. Call list_node_types first."
        return json.dumps(contracts.raw_schema(spec.schema_id) or {})

    async def list_middleware_types() -> str:
        """All agent middleware types with their config schemas (summarization, retries,
        fallback, PII, guardrails, budgets, HITL…). Attach via an agent node's
        config.middleware: [{type, config, enabled}]."""
        from forge.schemas import contracts

        out = {t: contracts.middleware_config_schema(t) for t in contracts.middleware_types()}
        return json.dumps(out)

    async def create_custom_workflow(name: str, description: str, definition_json: str) -> str:
        """Build ANY workflow shape — not just the canned patterns. `definition_json` is a
        JSON object: {"state": {...}, "entry_node": "start", "nodes": [{id,type,config}...],
        "edges": [{source,target}...]}. Node positions are auto-laid-out if omitted. State
        keys written by known node configs are auto-declared. The definition is VALIDATED:
        on failure you get field-pointer errors — fix the definition and call again.
        Idempotent by name (same name = update in place). Use list_node_types /
        get_node_schema first so configs are correct, and ALWAYS test_workflow afterwards."""
        try:
            definition = json.loads(definition_json)
        except json.JSONDecodeError as e:
            return f"ERROR: definition_json is not valid JSON: {e}"
        if not isinstance(definition, dict):
            return "ERROR: definition_json must be a JSON object."
        nodes = [dict(n) for n in (definition.get("nodes") or []) if isinstance(n, dict)]
        edges = [dict(e) for e in (definition.get("edges") or []) if isinstance(e, dict)]
        if not nodes:
            return "ERROR: definition has no nodes."
        for i, n in enumerate(nodes):
            if not isinstance(n.get("position"), dict):
                n["position"] = {"x": 60 + (i % 5) * 250, "y": 80 + (i // 5) * 170}
        state = definition.get("state") or _CHAT_STATE
        entry = definition.get("entry_node") or next((n["id"] for n in nodes if n.get("type") == "start"), "start")
        async with SessionLocal() as s:
            verb, err, warns = await _save_workflow(
                s, name=name, description=description or "Custom workflow built by the assistant.",
                nodes=nodes, edges=edges, state=state, entry_node=entry,
            )
        if err:
            return "ERROR: " + err + " — fix the definition and call create_custom_workflow again."
        shape = " → ".join(n["id"] for n in nodes[:6]) + ("…" if len(nodes) > 6 else "")
        return f"{verb} custom workflow '{name}' ({len(nodes)} nodes: {shape}).{_warn_note(warns)} Now VERIFY it with test_workflow."

    async def evaluate_build(user_request: str, what_was_built: str, test_results: str) -> str:
        """LLM judge for your own work: grades whether what you built actually satisfies the
        user's request, given the test_workflow results. Returns {verdict: pass|fail,
        issues: [...], next_steps: [...]}. Call this AFTER test_workflow; if the verdict is
        'fail', fix the issues and re-test before reporting success."""
        schema = {
            "title": "BuildEvaluation",
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail"]},
                "issues": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "issues"],
        }
        async with SessionLocal() as s:
            ctx = await build_compile_context(s, tenant_id=tenant_id, project_id=project_id)
        model = resolve_model(_assistant_model_ref(ctx), ctx)
        prompt = (
            "You are a strict QA judge for an AI-workflow build. Decide if the build satisfies the request.\n"
            f"USER REQUEST:\n{user_request}\n\nWHAT WAS BUILT:\n{what_was_built}\n\n"
            f"TEST RESULTS (from running the workflow):\n{test_results}\n\n"
            "Fail if: any requested behavior is missing, a test answer is wrong/empty/off-topic, "
            "routing went to the wrong branch, a requested HITL pause did not interrupt, or "
            "parameters the user asked for (model, instructions, folders, kinds) were ignored."
        )
        try:
            res = await model.with_structured_output(schema).ainvoke(prompt)
            return json.dumps(res if isinstance(res, dict) else getattr(res, "__dict__", {"verdict": "fail", "issues": ["judge returned an unexpected shape"]}))
        except Exception as e:  # noqa: BLE001 - judge unavailable (offline model)
            return json.dumps({"verdict": "pass", "issues": [f"judge unavailable ({e}); self-review the test results instead"]})

    async def delete_workflow(name_or_id: str) -> str:
        """Delete a workflow by its exact name or id (also removes its run history). Use this to
        clean up duplicates. If the name matches several workflows, this refuses and lists their
        ids so you can delete a specific one — never bulk-deletes on an ambiguous name."""
        async with SessionLocal() as s:
            wfs = await WorkflowService.list(s, tenant_id, project_id)
            by_id = next((w for w in wfs if w.id == name_or_id), None)
            matches = [by_id] if by_id else [w for w in wfs if w.name == name_or_id]
            if not matches:
                return f"No workflow named or id '{name_or_id}'. Use list_resources to see what exists."
            if len(matches) > 1:
                listing = "; ".join(f"{w.name} (id {w.id}, {w.status})" for w in matches)
                return (f"{len(matches)} workflows are named '{name_or_id}'. Re-call delete_workflow with a "
                        f"specific id to remove just one: {listing}")
            wf = matches[0]
            await WorkflowService.delete(s, wf)
        mutated.append({"kind": "workflow_deleted", "name": wf.name})
        return f"Deleted workflow '{wf.name}' (id {wf.id}) and its run history."

    def mk(fn, name):
        return StructuredTool.from_function(coroutine=fn, name=name, parse_docstring=False)

    return [
        mk(list_resources, "list_resources"),
        mk(describe_workflow, "describe_workflow"),
        mk(list_node_types, "list_node_types"),
        mk(get_node_schema, "get_node_schema"),
        mk(list_middleware_types, "list_middleware_types"),
        mk(create_agent_preset, "create_agent_preset"),
        mk(create_builtin_tool, "create_builtin_tool"),
        mk(create_rest_tool, "create_rest_tool"),
        mk(create_auth_provider, "create_auth_provider"),
        mk(add_qa_pair, "add_qa_pair"),
        mk(add_knowledge_text, "add_knowledge_text"),
        mk(create_grounded_workflow, "create_grounded_workflow"),
        mk(create_intent_router_workflow, "create_intent_router_workflow"),
        mk(create_custom_workflow, "create_custom_workflow"),
        mk(add_human_review, "add_human_review"),
        mk(test_workflow, "test_workflow"),
        mk(evaluate_build, "evaluate_build"),
        mk(delete_workflow, "delete_workflow"),
    ]


SYSTEM_PROMPT = """You are the Forge Assistant — an expert copilot embedded in Forge, a platform for \
building, testing, and shipping AI agents and workflows. You help the user build INTO their current project \
by calling your tools, and you explain how Forge works.

What you can do (use your tools to actually do these — don't just describe them):
- Inspect the project (list_resources, describe_workflow) and the platform itself (list_node_types, get_node_schema, list_middleware_types — the LIVE node/middleware catalog; read these before building anything custom).
- Create tools (create_rest_tool, create_builtin_tool — builtins include knowledge_search, which lets an agent search the knowledge base itself), reusable agent presets (create_agent_preset — takes instructions/model/tools), and auth providers (create_auth_provider).
- Grow the knowledge base (add_qa_pair — `kind` is a free-form category; add_knowledge_text — `folder` organizes documents).
- Build workflows (all idempotent by name):
  • create_grounded_workflow — start → retrieval (BOTH docs + Q&A) → grounded agent → end. Use for "answer from my knowledge base" chatbots, including "check my Q&A/FAQs and the knowledge base" — the retrieval step pulls both. HITL options: review_before_reply=True (human approves the draft before it's final) and approve_tools='a, b' (those tool calls pause for approval).
  • create_intent_router_workflow — start → classifier (structured output picks ONE intent) → router (one case per intent) → a specialist agent per intent + a general fallback. Use when different request types need different handling AND each request has a single intent.
  • create_custom_workflow — ANY other shape, from a full definition JSON (state/entry_node/nodes/edges). It validates and returns field-pointer errors you must fix. Use for multi-intent fan-out (classifier multi_label + router multi + synthesizer agent), tool_call pipelines, webhook flows, or anything the canned builders can't express. Read the forge-platform skill first.
- GIVE AGENTS KNOWLEDGE DIRECTLY. An agent node can search the project knowledge base itself — set its config.knowledge: {"rag": {"enabled": true, "folders": [...], "top_k": 4}, "qa": {"enabled": true, "kinds": [...]}}. This compiles to agent-callable tools (search_knowledge_base for documents, lookup_faq for curated answers), each toggled and scoped independently. PREFER this over wiring a separate retrieval node OR creating a knowledge_search Tool whenever the agent is conversational or may get multi-part questions — the agent searches per sub-question with its own phrasing instead of one fixed search on the whole message. Use the fixed retrieval NODE only when grounding must be guaranteed (the agent can't skip it).
- MULTI-INTENT questions ("one message asks two things"): prefer ONE agent with config.knowledge enabled (rag and/or qa) — it searches each sub-question separately and composes one answer — over a classifier→router, which answers only one part. For parallel specialists, use classifier multi_label=true + router multi=true + a synthesizer agent before end (see the forge-platform skill).
- Verify and judge your own work (test_workflow, then evaluate_build).
- Delete a workflow to clean up duplicates (delete_workflow — the run PAUSES for human approval before deleting).

How Forge concepts work, so you can explain them:
- A WORKFLOW is a graph of nodes wired start → … → end. Messages flow through. Common nodes: \
'retrieval' (RAG — pulls relevant knowledge-base docs + Q&A pairs for the question), \
'agent' (a model with tools and a system prompt that reasons and replies), 'classifier' (structured output picks one \
intent label into state), 'router' (branches on a state value via cases), 'human_input' (PAUSES the run with a real \
LangGraph interrupt until a human approves/rejects in the Playground). The recommended grounded chatbot is \
start → retrieval → agent → end, where the agent's prompt restricts it to the retrieved knowledge.
- HUMAN-IN-THE-LOOP means a real interrupt: either a 'human_input' node in the graph (review_before_reply on \
create_grounded_workflow, or add_human_review for an EXISTING workflow) or HumanInTheLoopMiddleware on the agent \
(approve_tools). NEVER fake HITL by telling the agent to "ask for approval" or "reply yes/no" in its instructions — \
prompt text does not pause anything. If the user says HITL/approval/review and the workflow exists, call \
add_human_review; if building new, pass review_before_reply=True. Then VERIFY with test_workflow that the run \
pauses (nodes_visited ends in '__interrupt__').
- A TOOL is an external capability (REST/GraphQL/builtin) an agent can call; response projection trims the payload to save tokens.
- An AUTH PROVIDER holds a reusable credential/session strategy that tools attach to.
- KNOWLEDGE = documents + Q&A pairs that ground answers.

Rules:
- When the user asks you to build/add/create something, CALL the appropriate tool, then confirm in one or two plain sentences what you did and what to do next (e.g. 'Test it in the Playground').
- WORK UNTIL IT'S CORRECT. For any build or change: (1) plan the steps with write_todos, (2) inspect what exists with list_resources, (3) build with the right tool, (4) VERIFY with test_workflow — try a realistic question, a greeting, an off-topic question, and EVERY branch/intent, (5) JUDGE with evaluate_build (pass it the user's request, what you built, and the test results) — if the verdict is 'fail', fix the issues and re-verify. Do not report success until both verification and the judge passed.
- MATCH THE REQUEST. Read what the user actually described and pick the right builder + parameters — do NOT default everything to create_grounded_workflow. If they say "use my Q&A/FAQs and the knowledge base", that's still create_grounded_workflow — its retrieval step already pulls BOTH Q&A pairs and documents. If they give specific agent instructions, persona, or a model, pass them via `instructions`/`model`. Name the workflow after what they asked for.
- TRIAGE FIRST (design for real conversations). Real users open with greetings and meta questions ("hi", "what can you do?", "how can you help me?") as often as real support questions. Do NOT pipe those straight into retrieval — they miss and dead-end at a "no relevant data → create a ticket" path, which is a terrible first impression. For any support/Q&A/RAG/ticketing workflow you design with create_custom_workflow, put a triage step right after start: a classifier (e.g. labels: general, support) → router that sends greetings/smalltalk/meta/capability questions to a small friendly agent that answers directly ("here's what I can help with…") and ends, and routes ONLY genuine product/support questions into the retrieval/ticket pipeline. (A single front agent with config.knowledge enabled — so it both chats AND searches the KB per sub-question — is an acceptable, often simpler, alternative.) When you VERIFY, always test a greeting and a "what can you do?" message and confirm they get a friendly direct reply — never the ticket path.
- AVOID DUPLICATES. Before creating a workflow, call list_resources to see what already exists. If a suitable workflow is already there, UPDATE it (reuse the same name — the builders update in place) or point the user to it. Only create a new one when the user clearly wants an additional, distinct workflow. Never create the same workflow twice in a session.
- When the user asks how something works or to explain their setup, call list_resources / describe_workflow first, then explain clearly and briefly.
- If the user wants to remove something or clean up duplicates, use delete_workflow (by exact name or id). Confirm the specific target first if the name is ambiguous.
- Be concise and concrete. Prefer doing over talking. Never claim you did something you didn't do via a tool."""


# The orchestrating assistant juggles ~19 tools and a long policy prompt — small models
# routinely ignore parameters like review_before_reply. Prefer the provider's strong
# model for the assistant itself (workflow agents keep the cheaper project default).
# Override per project via project.config.assistant_model.
_STRONG = {"openai": "openai:gpt-5.4", "anthropic": "anthropic:claude-sonnet-4-6"}

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "assistant_skills"


def _assistant_model_ref(ctx) -> str:
    """The model ref the assistant (and its judge) should run on: explicit project
    config first, then the provider's strong model, then the project default."""
    explicit = (getattr(ctx, "project_config", None) or {}).get("assistant_model")
    if explicit:
        return explicit
    creds = ctx.provider_credentials or {}
    return next((m for p, m in _STRONG.items() if p in creds), None) or ctx.default_model


class AssistantService:
    @staticmethod
    async def _build_agent(*, tenant_id: str, project_id: str, checkpointer, mutated: list):
        # Deep agent: adds planning (write_todos), virtual files, summarization, skills,
        # and HITL on top of the Forge tools — so it can take a multi-step request,
        # build, verify with test_workflow, judge with evaluate_build, and keep
        # iterating until the result is actually correct.
        from deepagents import FilesystemPermission, create_deep_agent
        from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
        from langchain.agents.middleware import ModelFallbackMiddleware

        async with SessionLocal() as s:
            ctx = await build_compile_context(s, tenant_id=tenant_id, project_id=project_id)
            proj = (await s.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
            ctx.project_config = (proj.config or {}) if proj else {}

        primary_ref = _assistant_model_ref(ctx)
        model = resolve_model(primary_ref, ctx)
        middleware = []
        # If the strong model is unavailable at call time (bad ref, provider outage),
        # fall back to the project's default model instead of failing the assistant.
        fallback_ref = ctx.default_model
        if fallback_ref and fallback_ref != primary_ref and not str(fallback_ref).startswith("fake"):
            middleware.append(ModelFallbackMiddleware(resolve_model(fallback_ref, ctx)))

        # Optionally prune the (large) tool set per turn to cut tool-schema tokens.
        from forge.config import settings as _settings
        if _settings.assistant_tool_selector:
            from langchain.agents.middleware import LLMToolSelectorMiddleware
            middleware.append(LLMToolSelectorMiddleware(model=model, max_tools=12))

        tools = build_assistant_tools(tenant_id, project_id, mutated)
        kwargs: dict[str, Any] = {
            "model": model,
            "tools": tools,
            "system_prompt": SYSTEM_PROMPT,
            "middleware": middleware,
        }
        # The forge-platform skill (progressive disclosure: name+description at startup,
        # full guide read on demand) carries deep how-to knowledge without prompt bloat.
        if _SKILLS_DIR.exists():
            kwargs["backend"] = CompositeBackend(
                default=StateBackend(),
                routes={"/skills/": FilesystemBackend(root_dir=str(_SKILLS_DIR), virtual_mode=True)},
            )
            kwargs["skills"] = ["/skills/"]
            kwargs["permissions"] = [
                FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
            ]
        # Destructive ops pause for human approval — REAL interrupts (needs checkpointer).
        if checkpointer is not None:
            kwargs["checkpointer"] = checkpointer
            kwargs["interrupt_on"] = {"delete_workflow": True}
        return create_deep_agent(**kwargs)

    @staticmethod
    async def _persist_trace(*, tenant_id: str, project_id: str, thread_id: str | None, tracer) -> None:
        """Persist one assistant turn as a Trace (name='assistant') + spans, so SPEND and
        the per-project reports include assistant usage like any workflow run."""
        import uuid as _uuid
        from datetime import datetime

        from forge.models import Span, Trace

        spans = tracer.ordered()
        if not spans:
            return
        tokens, cost = tracer.totals()
        started = min((s.start for s in spans), default=0.0)
        latency_ms = int((max((s.end or s.start for s in spans), default=started) - started) * 1000)
        try:
            async with SessionLocal() as s:
                trace = Trace(
                    tenant_id=tenant_id, project_id=project_id, workflow_id=None,
                    run_id=f"assistant-{_uuid.uuid4()}", thread_id=thread_id,
                    name="assistant", status="done",
                    started_at=datetime.utcnow(), ended_at=datetime.utcnow(),
                    latency_ms=latency_ms, total_tokens=tokens, total_cost_usd=cost,
                )
                s.add(trace)
                await s.flush()
                for sr in spans:
                    s.add(Span(
                        id=sr.id, tenant_id=tenant_id, trace_id=trace.id,
                        parent_span_id=sr.parent_id, name=sr.name, kind=sr.kind,
                        latency_ms=sr.latency_ms, model=sr.model,
                        input_tokens=sr.input_tokens, output_tokens=sr.output_tokens,
                        cost_usd=sr.cost_usd, error=sr.error, attributes=sr.attributes,
                    ))
                await s.commit()
        except Exception:  # noqa: BLE001 - tracing must never break the assistant
            pass

    @staticmethod
    async def _run(agent, payload, config, mutated: list) -> AsyncIterator[dict]:
        """Stream one agent turn: narration tokens, tool chips, todo updates, then either
        an interrupt frame (HITL approval needed) or done."""
        async for mode, chunk in agent.astream(payload, config, stream_mode=["messages", "updates"]):
            if mode == "messages":
                msg, _meta = chunk if isinstance(chunk, (list, tuple)) and len(chunk) == 2 else (chunk, {})
                # Only the agent's own narration — never echo tool-result or human messages.
                if getattr(msg, "type", "") not in ("ai", "AIMessageChunk"):
                    continue
                text = content_to_text(getattr(msg, "content", ""))
                if text:
                    # `id` segments the stream per AI message: the panel shows earlier
                    # segments as collapsible "thinking" and only the last as the answer.
                    yield {"event": "messages", "data": {"content": text, "id": getattr(msg, "id", None)}}
            elif mode == "updates" and isinstance(chunk, dict):
                for node_out in chunk.values():
                    if not isinstance(node_out, dict):
                        continue
                    # Plan visibility: surface write_todos state to the panel.
                    if node_out.get("todos") is not None:
                        yield {"event": "todos", "data": {"todos": jsonable(node_out["todos"])}}
                    for m in node_out.get("messages", []):
                        if getattr(m, "type", None) == "tool":
                            yield {"event": "tool", "data": {"name": getattr(m, "name", "tool"), "result": content_to_text(getattr(m, "content", ""))[:2000]}}

        snapshot = await agent.aget_state(config)
        interrupts = [
            jsonable(getattr(t, "interrupts", None))
            for t in getattr(snapshot, "tasks", [])
            if getattr(t, "interrupts", None)
        ]
        if interrupts:
            yield {"event": "interrupt", "data": {"interrupts": interrupts}}
        yield {"event": "done", "data": {"mutated": mutated, "interrupted": bool(interrupts)}}

    @staticmethod
    async def stream(
        *, tenant_id: str, project_id: str, messages: list[dict],
        thread_id: str | None = None, checkpointer=None,
    ) -> AsyncIterator[dict]:
        mutated: list = []
        try:
            agent = await AssistantService._build_agent(
                tenant_id=tenant_id, project_id=project_id, checkpointer=checkpointer, mutated=mutated
            )
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": {"message": f"assistant init failed: {e}"}}
            return

        lc_messages = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages if m.get("content")]
        from forge.tracing.tracer import ForgeTracer

        tracer = ForgeTracer()
        # Generous recursion limit: the build→verify→judge→fix loop takes many steps.
        config: dict[str, Any] = {"recursion_limit": 100, "callbacks": [tracer]}
        if checkpointer is not None and thread_id:
            # Persistent conversation: the checkpointer holds history/todos/files for the
            # thread, so the frontend sends ONLY the new message.
            config["configurable"] = {"thread_id": f"assistant:{project_id}:{thread_id}"}
        try:
            async for frame in AssistantService._run(agent, {"messages": lc_messages}, config, mutated):
                yield frame
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": {"message": str(e)}}
        finally:
            await AssistantService._persist_trace(tenant_id=tenant_id, project_id=project_id, thread_id=thread_id, tracer=tracer)

    @staticmethod
    async def resume(
        *, tenant_id: str, project_id: str, thread_id: str, decision: str = "approve",
        checkpointer=None,
    ) -> AsyncIterator[dict]:
        """Resume after a HITL interrupt (e.g. delete_workflow approval)."""
        from langgraph.types import Command

        mutated: list = []
        if checkpointer is None or not thread_id:
            yield {"event": "error", "data": {"message": "resume requires a persistent assistant thread"}}
            return
        try:
            agent = await AssistantService._build_agent(
                tenant_id=tenant_id, project_id=project_id, checkpointer=checkpointer, mutated=mutated
            )
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": {"message": f"assistant init failed: {e}"}}
            return
        from forge.tracing.tracer import ForgeTracer

        tracer = ForgeTracer()
        config: dict[str, Any] = {
            "recursion_limit": 100,
            "configurable": {"thread_id": f"assistant:{project_id}:{thread_id}"},
            "callbacks": [tracer],
        }
        # HumanInTheLoopMiddleware resume shape: {"decisions": [{"type": "approve"|"reject"}]}
        value = {"decisions": [{"type": decision if decision in ("approve", "reject", "edit", "respond") else "approve"}]}
        try:
            async for frame in AssistantService._run(agent, Command(resume=value), config, mutated):
                yield frame
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": {"message": str(e)}}
        finally:
            await AssistantService._persist_trace(tenant_id=tenant_id, project_id=project_id, thread_id=thread_id, tracer=tracer)
