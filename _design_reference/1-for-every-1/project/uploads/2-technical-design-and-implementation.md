# AI Agent Platform — Technical Design & Implementation Plan
**Version:** 1.0 · **Date:** June 2026 · **Audience:** Claude Code (build agent) and engineers

> Document 2 of 3. Read Document 1 (Research & Technology Decisions) first for the *why*. This document is the *what* and *how*, and ends with a phased build plan you can execute one phase at a time. Codename for the product in this doc: **Forge**.

---

## Table of contents
1. System overview
2. Tech stack & versions
3. Glossary & the configuration hierarchy
4. Data model (Postgres)
5. Core abstraction: Workflow Definition (canvas vs executable)
6. Core abstraction: Node Type Registry & the compiler
7. Node type catalog
8. Core abstraction: the Middleware-Stack Compiler
9. Agent node vs Deep Agent node
10. Core abstraction: Tool materialization
11. Core abstraction: Auth Providers & the Resolver
12. Secrets & encryption
13. Sandbox interface
14. RAG: EmbeddingStore, ingestion, Q&A
15. Tracing layer
16. Execution engine & streaming
17. MCP: consuming and exposing
18. Chat widget architecture
19. The in-product build assistant (meta-agent)
20. API surface
21. Multi-tenancy & security
22. Repository structure
23. End-to-end worked example
24. Testing strategy
25. Phased build plan (Phase 0–7)

---

## 1. System overview

```
                          ┌──────────────────────────────────────────────┐
   Browser (Next.js)      │                 Forge API (FastAPI)            │
  ┌──────────────────┐    │  ┌───────────┐  ┌───────────┐  ┌────────────┐ │
  │ Builder canvas    │◄──►│  │ REST      │  │ SSE stream│  │ MCP server │ │
  │ Config panels     │    │  │ routers   │  │ endpoints │  │ (FastMCP)  │ │
  │ Test playground   │    │  └─────┬─────┘  └─────┬─────┘  └─────┬──────┘ │
  │ Trace explorer    │    │        │              │              │        │
  │ Widget config     │    │  ┌─────▼──────────────▼──────────────▼──────┐ │
  └──────────────────┘    │  │            Service layer                  │ │
                          │  │  ProjectSvc AgentSvc ToolSvc AuthSvc       │ │
  Embedded widget         │  │  WorkflowSvc RunSvc KnowledgeSvc WidgetSvc │ │
  (iframe + loader.js) ──►│  └─────┬───────────────┬───────────────┬─────┘ │
                          │        │               │               │       │
                          │  ┌─────▼─────┐  ┌──────▼──────┐  ┌─────▼─────┐ │
                          │  │ Compiler  │  │ Execution    │  │ Tracer    │ │
                          │  │ (JSON→    │  │ engine       │  │ (callback │ │
                          │  │ LangGraph)│  │ (LangGraph)  │  │ handler)  │ │
                          │  └─────┬─────┘  └──────┬───────┘  └─────┬─────┘ │
                          └────────┼───────────────┼───────────────┼───────┘
                                   │               │               │
        ┌──────────────┬──────────┴───┬───────────┴──────┬─────────┴─────┐
   ┌────▼────┐   ┌─────▼─────┐  ┌──────▼──────┐   ┌───────▼─────┐  ┌──────▼──────┐
   │Postgres │   │  Redis    │  │ Auth Resolver│   │  Sandbox    │  │  Secrets    │
   │+pgvector│   │ cache/arq │  │ + token cache│   │ subproc/E2B │  │  store/KMS  │
   │+checkpts│   └───────────┘  └──────────────┘   └─────────────┘  └─────────────┘
   └─────────┘

   arq workers (separate process, same codebase): ingestion, embeddings, trace flush, async subagents
```

Data flow for a run: client opens SSE -> RunSvc loads the workflow's **executable JSON** -> Compiler builds a `CompiledStateGraph` (with `AsyncPostgresSaver` checkpointer + `PostgresStore` + the Tracer callback) -> engine `astream`s -> SSE frames pushed to client -> tool calls resolve auth via the Resolver and run in a Sandbox if code -> spans flushed to Postgres via Redis.

---

## 2. Tech stack & versions

Pin exact minors; re-verify each phase.

```
# Python (API + workers)
python = ">=3.11,<3.14"
fastapi, uvicorn[standard], pydantic v2
sqlalchemy[asyncio] >=2, alembic, psycopg[binary,pool] >=3
redis >=5, arq
langchain >=1.3,<2
langgraph >=1.2,<2
langgraph-checkpoint-postgres >=3,<4
deepagents >=0.5,<1
langchain-mcp-adapters >=0.2,<1
fastmcp >=3,<4
langchain-openai, langchain-anthropic, langchain-google-genai  # native big three
langchain-litellm, langchain-openrouter                        # universal gateways
pgvector, jmespath, httpx, cryptography, RestrictedPython, python-jose[cryptography], passlib[bcrypt]
# Frontend
next, react, @xyflow/react (React Flow v12), tailwindcss, shadcn/ui, @tanstack/react-query, zustand, dagre, monaco-editor
```

---

## 3. Glossary & the configuration hierarchy

The user explicitly asked for config "at every level." These are the levels and what lives at each. Each level is a JSON document validated against a JSON Schema we own; the same schema drives the UI form (Doc 3).

| Level | Object | Configures |
|---|---|---|
| **Project** | `projects.config` | default model + provider creds binding, default middleware, budget caps (tokens/$/run), allowed models, data region, RAG defaults (embedding model, chunking), feature flags (code nodes on/off), tracing retention |
| **Tool** | `tools.config` | kind (rest/graphql/code/mcp/builtin), request schema (per-field, in: path/query/header/body), response schema (per-field descriptions + `include_in_llm`), projection (JMESPath/field list), auth_provider binding, rate limit, timeout, retry, caching |
| **Auth Provider** | `auth_providers.config` | kind (csrf_session/oauth2_client_credentials/bearer/basic/api_key/custom_script), token-fetch recipe, extraction rules, injection rules, credentials ref, cache TTL, refresh_on |
| **Workflow** | `workflows.executable` + `.canvas` | nodes[], edges[], state schema (fields + reducers), entry node, global middleware, error policy, concurrency, timeout |
| **Agent (node or preset)** | node `config` / `agents.config` | flavor (agent/deep_agent), model, system prompt (static/dynamic), tools, response_format, middleware stack (the big one), state extensions, memory (store namespace), name |
| **Widget** | `widget_configs` | theme tokens, launcher, allowed origins, host-variable injection rules, greeting, suggested prompts, auth mode, rate limit, identity verification (HMAC) |
| **Run** (ephemeral) | invocation `context` | thread_id, user_external_id, per-user secrets (CSRF/session), locale, feature overrides |

Rule of precedence: Run context > Node config > Workflow config > Project config > Platform default.

---

## 4. Data model (Postgres)

All leaf tables carry `tenant_id uuid NOT NULL` (denormalized) and have an RLS policy `USING (tenant_id = current_setting('app.tenant_id')::uuid)`. `id uuid DEFAULT gen_random_uuid()`, `created_at/updated_at timestamptz`.

```sql
-- Tenancy & identity
tenants(id, name, plan, region, settings jsonb)
users(id, tenant_id, email UNIQUE, password_hash, role, status, last_login_at)
api_keys(id, tenant_id, user_id, name, hashed_key, scopes text[], last_used_at, revoked_at)

-- Projects & presets
projects(id, tenant_id, name, slug, description, config jsonb, archived bool)
agents(id, tenant_id, project_id, name, config jsonb, version int)        -- reusable agent presets

-- Tools & auth
tools(id, tenant_id, project_id, name, kind, config jsonb, auth_provider_id uuid NULL,
      enabled bool, version int)
auth_providers(id, tenant_id, project_id, name, kind, config jsonb, credentials_ref text)
mcp_clients(id, tenant_id, project_id, name, transport, url text NULL, command text NULL,
            args jsonb, headers_ref text NULL, enabled bool)

-- Workflows (canvas + executable kept separate)
workflows(id, tenant_id, project_id, name, description, canvas jsonb, executable jsonb,
          status, active_version int)
workflow_versions(id, tenant_id, workflow_id, version int, canvas jsonb, executable jsonb,
                  created_by, note)

-- Execution & threads
threads(id, tenant_id, project_id, workflow_id, user_external_id, lg_thread_id text, title,
        status, metadata jsonb)
runs(id, tenant_id, project_id, workflow_id, thread_id, status,            -- queued|running|interrupted|done|error
     input jsonb, output jsonb, error text, started_at, ended_at,
     total_tokens int, total_cost_usd numeric(12,6))

-- Tracing
traces(id, tenant_id, project_id, workflow_id, run_id, thread_id, name, status,
       started_at, ended_at, latency_ms int, total_tokens int, total_cost_usd numeric(12,6),
       metadata jsonb, error text)
spans(id, tenant_id, trace_id, parent_span_id, name,
      kind,                                                                 -- llm|tool|chain|retriever|agent|node|subagent
      started_at, ended_at, latency_ms int, input jsonb, output jsonb, model text,
      input_tokens int, output_tokens int, cost_usd numeric(12,6), error text, attributes jsonb)

-- Knowledge (pgvector)
kb_sources(id, tenant_id, project_id, kind, name, uri, status, metadata jsonb)  -- file|url|s3|text|api
kb_documents(id, tenant_id, project_id, source_id, title, metadata jsonb)
kb_chunks(id, tenant_id, project_id, document_id, chunk_idx int, content text,
          metadata jsonb, embedding vector(1536), embedding_model text, tokens int,
          fts tsvector)
qa_pairs(id, tenant_id, project_id, question text, answer text, kind, tags text[],   -- faq|error_workaround
         q_embedding vector(1536), upvotes int, last_used_at)

-- Widget & secrets & audit
widget_configs(id, tenant_id, project_id, workflow_id, theme jsonb, allowed_origins text[],
               host_variables jsonb, settings jsonb, public_key text)
secrets(id, tenant_id, project_id, name, kind, encrypted_value bytea, version int)
audit_log(id, tenant_id, actor_id, action, resource_type, resource_id, ip, detail jsonb, at)

-- indexes
CREATE INDEX ON kb_chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
CREATE INDEX ON kb_chunks (tenant_id, project_id);
CREATE INDEX ON kb_chunks USING gin (fts);
CREATE INDEX ON qa_pairs USING hnsw (q_embedding vector_cosine_ops);
CREATE INDEX ON traces (tenant_id, started_at DESC);
CREATE INDEX ON spans (trace_id);
```
Checkpoints are managed by `langgraph-checkpoint-postgres` (`await checkpointer.setup()` creates its own tables).

---

## 5. Workflow Definition (canvas vs executable)

Two documents per workflow, always kept in sync by the save endpoint.

**Canvas JSON** (React Flow round-trip; UI owns it):
```json
{"nodes": [{"id":"n1","type":"agent","position":{"x":120,"y":80},"data":{ "...form state..." }}],
 "edges": [{"id":"e1","source":"n1","sourceHandle":"out","target":"n2","targetHandle":"in"}],
 "viewport": {"x":0,"y":0,"zoom":1}}
```

**Executable JSON** (compiler input; backend owns the schema):
```json
{
  "id": "wf_abc", "version": 7,
  "state": {
    "messages": {"type": "list[message]", "reducer": "add_messages"},
    "findings": {"type": "list[str]", "reducer": "add"},
    "intent": {"type": "str", "reducer": "last"}
  },
  "entry_node": "router",
  "global_middleware": [ {"type": "model_call_limit", "config": {"run_limit": 25}} ],
  "nodes": [
    {"id":"router","type":"router",
     "config":{"expression":"intent","cases":{"billing":"billing_agent","tech":"tech_agent"},"default":"fallback"}},
    {"id":"billing_agent","type":"agent",
     "config":{"flavor":"agent","model":"anthropic:claude-sonnet-4-6","system_prompt":"...",
               "tools":["tool_get_quote"],
               "middleware":[{"type":"summarization","config":{"trigger":["tokens",4000]}},
                             {"type":"tool_call_limit","config":{"tool_name":"tool_get_quote","run_limit":3}}]}}
  ],
  "edges": [{"source":"billing_agent","target":"END"}]
}
```

**Validation on save** (reject with field-level errors): every node config valid against its node-type schema; all required input handles connected; no orphan nodes; entry node present; cycles only on nodes that allow them (`agent`/`deep_agent` legitimately loop); referenced tool/auth/model IDs exist and are enabled; budget caps present if project requires.

---

## 6. Node Type Registry & the compiler

```python
# registry.py
NodeFactory = Callable[[dict, "CompileContext"], Callable]   # returns a LangGraph node callable
NODE_REGISTRY: dict[str, "NodeSpec"] = {}

@dataclass
class NodeSpec:
    type: str
    json_schema: dict                 # drives validation + the UI form
    input_ports: list["Port"]         # typed handles
    output_ports: list["Port"]
    factory: NodeFactory
    allows_cycle: bool = False

def register(spec: NodeSpec): NODE_REGISTRY[spec.type] = spec
```

```python
# compiler.py
def compile_workflow(definition: dict, ctx: CompileContext) -> CompiledStateGraph:
    StateSchema = build_state_typeddict(definition["state"])     # TypedDict at runtime, NOT pydantic
    builder = StateGraph(StateSchema)
    for n in definition["nodes"]:
        spec = NODE_REGISTRY[n["type"]]
        builder.add_node(n["id"], spec.factory(n["config"], ctx))
    for e in definition["edges"]:
        if e.get("condition") or e.get("branches"):
            builder.add_conditional_edges(e["source"], make_router(e), e.get("branches"))
        else:
            builder.add_edge(e["source"], "END" if e["target"]=="END" else e["target"])
    builder.add_edge(START, definition["entry_node"])
    return builder.compile(checkpointer=ctx.checkpointer, store=ctx.store)

def build_state_typeddict(state_cfg: dict) -> type:
    REDUCERS = {"add_messages": add_messages, "add": operator.add, "last": lambda a,b: b}
    annotations = {}
    for field, spec in state_cfg.items():
        py = PY_TYPES[spec["type"]]                       # map "list[message]" -> list, etc.
        annotations[field] = Annotated[py, REDUCERS[spec["reducer"]]]
    return type("WorkflowState", (AgentState,), {"__annotations__": annotations})
```

`CompileContext` carries: tenant_id, project_id, checkpointer, store, tracer callback, the resolved tool registry (materialized `StructuredTool`s), the auth resolver, the sandbox, and model-provider credential bindings. Compiled graphs are cached by `(workflow_id, version)` and invalidated on save.

---

## 7. Node type catalog

Each ships a JSON Schema + typed ports. `IOType ∈ {messages, text, json, tool, embedding, vector, any, control}`.

| type | purpose | key config | ports |
|---|---|---|---|
| `start` | entry marker | — | out: control |
| `end` | terminal | — | in: control |
| `agent` | `create_agent` ReAct agent | flavor, model, system_prompt(static/dynamic), tools[], response_format, middleware[], state_ext | in/out: messages |
| `deep_agent` | `create_deep_agent` harness | + planning, subagents[], filesystem backend, sandbox, skills, permissions | in/out: messages |
| `llm` | single model call, no tools | model, prompt template, output parser/structured | in: text/json, out: text/json |
| `tool_call` | run a specific tool (ToolNode) | tool_id, input mapping, handle_tool_errors | in: json, out: json |
| `router` | conditional branch | expression (RestrictedPython over state), cases->node, default | in: any, out: N control |
| `retrieval` | RAG query | source filter, top_k, hybrid?, rerank?, projection | in: text, out: json (docs) |
| `qa_lookup` | semantic Q&A pair match | threshold, kind filter | in: text, out: text |
| `human_input` | HITL pause via `interrupt()` | prompt, schema, allowed_decisions | in: any, out: any |
| `code` | sandboxed transform | language, source, input/output schema, sandbox tier | in: json, out: json |
| `transform` | declarative data map | JMESPath/JQ expression | in: json, out: json |
| `subworkflow` | embed another workflow as subgraph / `CompiledSubAgent` | workflow_id, version, io mapping | in/out: messages/json |
| `parallel_fanout` | map over a list -> `Send[]` | over (state key), child node, item key | in: json, out: control[] |
| `join` | wait-for-all / reduce | reducer | in: control[], out: json |
| `loop` | bounded iteration | condition, max_iter | in/out: any |
| `webhook_out` | call external URL (non-LLM) | url, method, auth_provider, body map | in: json, out: json |
| `emit_event` | push custom SSE frame | channel, payload map | in: any, out: any |

Add new node types without touching the compiler — register a `NodeSpec`. The UI palette is generated from the registry.

---

## 8. The Middleware-Stack Compiler

The heart of "limitless agent customization." An agent/deep_agent node's `middleware: [{type, config}]` list compiles to a concrete `list[AgentMiddleware]`.

```python
# middleware_compiler.py
MW_BUILDERS: dict[str, Callable[[dict, CompileContext], AgentMiddleware]] = {
  "summarization":   lambda c,ctx: SummarizationMiddleware(model=resolve_model(c.get("model"), ctx),
                          trigger=_ctxsize(c["trigger"]), keep=_ctxsize(c.get("keep",("messages",20)))),
  "human_in_the_loop": lambda c,ctx: HumanInTheLoopMiddleware(interrupt_on=c["interrupt_on"]),
  "model_call_limit": lambda c,ctx: ModelCallLimitMiddleware(**c),
  "tool_call_limit":  lambda c,ctx: ToolCallLimitMiddleware(**c),
  "model_fallback":   lambda c,ctx: ModelFallbackMiddleware(*[resolve_model(m,ctx) for m in c["models"]]),
  "pii":              lambda c,ctx: PIIMiddleware(c["pii_type"], strategy=c.get("strategy","redact"),
                          detector=c.get("detector"), apply_to_input=c.get("apply_to_input",True),
                          apply_to_output=c.get("apply_to_output",False)),
  "todo":             lambda c,ctx: TodoListMiddleware(**c),
  "llm_tool_selector":lambda c,ctx: LLMToolSelectorMiddleware(model=resolve_model(c.get("model"),ctx),
                          max_tools=c.get("max_tools"), always_include=c.get("always_include",[])),
  "tool_retry":       lambda c,ctx: ToolRetryMiddleware(**c),
  "model_retry":      lambda c,ctx: ModelRetryMiddleware(**c),
  "tool_emulator":    lambda c,ctx: LLMToolEmulator(tools=c.get("tools"), model=resolve_model(c.get("model"),ctx)),
  "context_editing":  lambda c,ctx: ContextEditingMiddleware(edits=[ClearToolUsesEdit(**e) for e in c["edits"]]),
  "anthropic_prompt_caching": lambda c,ctx: AnthropicPromptCachingMiddleware(**c),
  "openai_moderation": lambda c,ctx: OpenAIModerationMiddleware(**c),
  # custom / advanced:
  "dynamic_model_by_state": lambda c,ctx: build_dynamic_model_mw(c, ctx),   # wrap_model_call from rules
  "tool_filter_by_context": lambda c,ctx: build_tool_filter_mw(c, ctx),     # wrap_model_call filter
  "guardrail_regex":  lambda c,ctx: build_guardrail_mw(c, ctx),             # after_model
  "request_signing":  lambda c,ctx: build_signing_mw(c, ctx),              # wrap_tool_call
  "tenant_budget":    lambda c,ctx: build_budget_mw(c, ctx),                # before_model: stop if over $ cap
}

def build_middleware(stack: list[dict], ctx: CompileContext) -> list:
    return [MW_BUILDERS[m["type"]](m.get("config", {}), ctx) for m in stack]
```

The "custom/advanced" builders generate middleware from declarative rules so non-coders get power without code:
- `dynamic_model_by_state`: rules like `[{when:"len(messages)>10", use:"openai:gpt-5.4"}], default:"gpt-5.4-mini"` -> a `@wrap_model_call` closure.
- `tool_filter_by_context`: `{expose_when:{role:["admin"]}, tools:[...]}` -> filter via `request.override(tools=...)` reading `runtime.context`.
- `tenant_budget`: reads accumulated cost for the thread (from `traces`), raises/ends when over the project cap.

Project-level default middleware is prepended to every agent's stack at compile time.

---

## 9. Agent node vs Deep Agent node

```python
# nodes/agent_node.py
def agent_factory(config: dict, ctx: CompileContext):
    tools = ctx.tools_for(config.get("tools", []))
    mw    = build_middleware(ctx.project_default_mw + config.get("middleware", []), ctx)
    model = resolve_model(config["model"], ctx)
    common = dict(model=model, tools=tools, system_prompt=build_prompt(config), middleware=mw,
                  checkpointer=ctx.checkpointer, store=ctx.store, name=config.get("name"))
    if config.get("response_format"):
        common["response_format"] = build_response_format(config["response_format"])
    if config["flavor"] == "deep_agent":
        from deepagents import create_deep_agent
        agent = create_deep_agent(**common, backend=ctx.sandbox_backend_for(config),
                                  subagents=build_subagents(config.get("subagents", []), ctx))
    else:
        from langchain.agents import create_agent
        agent = create_agent(**common)
    return agent   # a compiled graph usable as a node
```

`build_subagents` turns each subagent config into a dict (`{name,description,system_prompt,tools,model,middleware}`) for `SubAgentMiddleware`, or wraps a referenced workflow as `CompiledSubAgent(runnable=compile_workflow(...))`.

`resolve_model`: parse the model string; for the big three use the native package; otherwise route via `langchain-litellm`/`langchain-openrouter`. Bind provider credentials from the project's secret refs. Use the model `.profile` to gate UI options and to drive `fraction` triggers.

---

## 10. Tool materialization

A `tools` row's `config` becomes a runnable `StructuredTool` at compile time. Supported `kind`: `rest_api`, `graphql`, `code`, `mcp`, `builtin`.

**Tool config (rest_api example):**
```json
{
  "kind": "rest_api", "name": "tool_get_quote",
  "description": "Fetch a quote by ID from the client's quoting portal.",
  "request": {
    "method": "GET",
    "url_template": "https://portal.example.com/api/quotes/{quote_id}",
    "fields": [
      {"path":"quote_id","type":"string","in":"path","required":true,
       "description":"The quote identifier","llm_visible":true},
      {"path":"include","type":"string","in":"query","required":false,
       "description":"Comma-separated expansions","llm_visible":false,"default":"totals,customer"}
    ],
    "headers": [{"name":"Accept","value":"application/json"}]
  },
  "response": {
    "fields": [
      {"path":"data.totals.subtotal","description":"Pre-tax subtotal (USD)","include_in_llm":true},
      {"path":"data.totals.grand_total","description":"Grand total (USD)","include_in_llm":true},
      {"path":"data.line_items","description":"Per-line items","include_in_llm":false}
    ],
    "projection_jmespath": "data.{subtotal: totals.subtotal, total: totals.grand_total, customer: customer.name}"
  },
  "auth_provider_id": "ap_portal_session",
  "rate_limit": {"per_minute": 60}, "timeout_seconds": 30,
  "cache": {"ttl_seconds": 0}
}
```

**Materialization:**
```python
def build_rest_tool(cfg: dict, ctx: CompileContext) -> StructuredTool:
    # 1) LLM-visible args -> Pydantic args_schema (NOT state schema)
    fields = {f["path"]: (PY[f["type"]], Field(description=f["description"],
                          default=f.get("default", ... if f["required"] else None)))
              for f in cfg["request"]["fields"] if f["llm_visible"]}
    ArgsSchema = create_model(f"{cfg['name']}_args", **fields)

    async def _call(runtime: ToolRuntime, **kwargs):
        auth = await ctx.auth_resolver.resolve(cfg["auth_provider_id"], context=runtime.context)
        url = render(cfg["request"]["url_template"], {**defaults(cfg), **kwargs})
        headers = render_headers(cfg, kwargs) | auth.headers
        params  = collect(cfg, kwargs, where="query")
        body    = collect(cfg, kwargs, where="body") or None
        runtime.stream_writer(f"Calling {cfg['name']}...")
        async with httpx.AsyncClient(cookies=auth.cookies, timeout=cfg["timeout_seconds"]) as c:
            r = await c.request(cfg["request"]["method"], url, headers=headers, params=params, json=body)
            if r.status_code in (401,403):
                await ctx.auth_resolver.invalidate(cfg["auth_provider_id"], runtime.context)
                auth = await ctx.auth_resolver.resolve(cfg["auth_provider_id"], context=runtime.context, force=True)
                r = await c.request(cfg["request"]["method"], url, headers=render_headers(cfg,kwargs)|auth.headers, params=params, json=body)
            r.raise_for_status()
            data = r.json()
        return project_response(data, cfg["response"])    # JMESPath first, else field-list, else full

    return StructuredTool.from_function(coroutine=_call, name=cfg["name"],
        description=cfg["description"], args_schema=ArgsSchema)
```
`project_response` cuts payloads before they reach the LLM — the primary token lever. `code` tools run their callable in the Sandbox; `mcp` tools come from `MultiServerMCPClient.get_tools()` with a tool interceptor that injects `runtime.context`; `builtin` tools are first-party (web search via Tavily/Exa, current time, etc.); `graphql` is rest_api with a query template + variables.

**Tool test endpoint:** `POST /tools/:id/test` materializes the tool, runs `_call` once with user-supplied args + a test context, and returns raw response, projected response, token estimate (raw vs projected), and latency.

---

## 11. Auth Providers & the Resolver

**Auth Provider config (csrf_session):**
```json
{
  "kind": "csrf_session",
  "credentials_ref": "secret://proj/portal_creds",
  "token_fetch": {
    "method": "POST", "url": "https://portal.example.com/api/auth/login",
    "headers": {"Content-Type":"application/json"},
    "body": {"username":"{{cred.username}}","password":"{{cred.password}}"}
  },
  "extract": [
    {"name":"csrf","from":"header","header":"X-CSRF-Token"},
    {"name":"session","from":"cookie","cookie":"SESSIONID"},
    {"name":"ttl","from":"json","json_path":"expires_in","kind":"ttl"}
  ],
  "inject": [
    {"to":"header","name":"X-CSRF-Token","value":"{{extracted.csrf}}"},
    {"to":"cookie","name":"SESSIONID","value":"{{extracted.session}}"}
  ],
  "cache_ttl_seconds": 1800, "refresh_on": [401,403]
}
```
Other kinds: `oauth2_client_credentials` (token endpoint + client id/secret + scope; refresh via refresh_token), `bearer`, `basic`, `api_key`, `custom_script` (RestrictedPython that returns `{headers, cookies, ttl}` — advanced, audited).

**Resolver:**
```python
class AuthResolver:
    async def resolve(self, provider_id, *, context: dict, force=False) -> ResolvedAuth:
        provider = await load_provider(provider_id)
        key = f"auth:{provider_id}:{ctx_hash(context, provider)}"   # may include per-user dims
        if not force and (cached := await self.cache.get(key)) and not cached.expired:
            return cached
        creds = await self.secrets.read(provider.credentials_ref)
        # per-user secrets (CSRF/session pulled by widget) arrive in context, never stored:
        merged = {"cred": creds, "ctx": context}
        if provider.kind in ("bearer","api_key","basic"):
            resolved = static_auth(provider, merged)
        else:
            resp = await http.request(**render(provider.token_fetch, merged))
            extracted = {e["name"]: extract(resp, e) for e in provider.extract}
            resolved = ResolvedAuth(
                headers=render_injections(provider.inject, extracted, where="header"),
                cookies=render_injections(provider.inject, extracted, where="cookie"),
                expires_at=now()+ (extracted.get("ttl") or provider.cache_ttl_seconds))
        await self.cache.set(key, resolved, ttl=resolved.ttl); return resolved
```
Cookie jar via httpx `Cookies` (RFC 6265). Token cache in Redis keyed by `(tenant, provider, context-hash)`. Test endpoint `POST /auth-providers/:id/test` runs the fetch and shows extracted values (secrets masked).

---

## 12. Secrets & encryption

`secrets.encrypted_value` = Fernet(`cryptography`) ciphertext; master key from env/KMS, never in DB. Decrypt only at runtime inside the Resolver/tool. Support `credentials_ref` schemes: `secret://proj/<name>` (our store) and `vault://<path>` (HashiCorp Vault/OpenBao for enterprise). Audit every read into `audit_log`. Rotation: versioned secrets; re-encrypt on key rotation job. **Prohibited:** the platform never logs decrypted secrets, never puts them in URLs/query strings, never returns them in API responses (mask to `••••1234`).

---

## 13. Sandbox interface

```python
class Sandbox(Protocol):
    async def run(self, *, language: str, source: str, inputs: dict,
                  limits: Limits, egress_allowlist: list[str]) -> SandboxResult: ...

class SubprocessSandbox:   # MVP: -I -S, setrlimit (CPU/AS/NOFILE/NPROC), 30s timeout, egress proxy
    ...
class DockerSandbox:       # via ShellToolMiddleware(DockerExecutionPolicy) or direct container
    ...
class RemoteSandbox:       # E2B/Modal/Daytona/Runloop via deepagents backend, sandbox-as-a-tool
    ...
```
Selected per project tier and per `code` node config. Deep Agent nodes pass a `backend=` built here. Egress always goes through a per-tenant allowlist proxy. Resource defaults: 10s CPU, 512MB, 32 FDs, 16 procs, no inbound network.

---

## 14. RAG: EmbeddingStore, ingestion, Q&A

```python
class EmbeddingStore(Protocol):
    async def upsert(self, tenant_id, project_id, chunks: list[Chunk]) -> None: ...
    async def query(self, tenant_id, project_id, vector, *, top_k, filters, hybrid_text=None) -> list[Hit]: ...
    async def delete_by_doc(self, tenant_id, project_id, document_id) -> None: ...

class PgVectorStore(EmbeddingStore): ...   # default
class QdrantStore(EmbeddingStore): ...     # phase 2, shard key = tenant_id
```
**Ingestion (arq job):** source -> loader (`UnstructuredFileLoader`, `PyPDFLoader`, `WebBaseLoader`, `S3`) -> splitter (`RecursiveCharacterTextSplitter` 1000/200 or `MarkdownHeaderTextSplitter`) -> embed (batch) -> `upsert` with `embedding_model` recorded -> update `kb_sources.status`. **Hybrid search:** combine pgvector cosine + `fts @@ plainto_tsquery` via reciprocal rank fusion; optional rerank. **Q&A:** `qa_pairs` stores question+answer (kind faq | error_workaround), embed question; `qa_lookup` node returns the answer when cosine >= threshold (cheap deflection before invoking the model). `retrieval` node returns projected doc chunks into state.

---

## 15. Tracing layer

```python
class ForgeTracer(BaseCallbackHandler):
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw): ...   # node/agent span
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id, **kw): ... # llm span
    def on_chat_model_end(self, response, *, run_id, **kw):
        usage = response_usage(response)   # from usage_metadata
        self.sink.end(run_id, output=..., input_tokens=usage.in_, output_tokens=usage.out_,
                      cost_usd=price(usage, model_of(response)))
    def on_tool_start/on_tool_end/on_retriever_start/on_retriever_end(...): ...
```
Attach `config={"callbacks":[ForgeTracer(run_id, tenant)]}` to every `astream`. `sink` batches spans to Redis; an arq worker flushes to `spans`/`traces`. Span attributes follow OTEL GenAI conventions. `model_pricing.json` maps model -> input/output $ per 1M tokens (editable in admin). Roll up trace totals into `runs`. Optional: emit OTLP to a self-hosted Langfuse when a project enables it.

---

## 16. Execution engine & streaming

```python
async def stream_run(run_id) -> AsyncIterator[ServerSentEvent]:
    run = await RunSvc.load(run_id)
    graph = await CompilerCache.get(run.workflow_id, run.version, ctx=ctx_for(run))
    config = {"configurable": {"thread_id": run.lg_thread_id, "checkpoint_ns": ns(run)},
              "callbacks": [ForgeTracer(run_id, run.tenant_id)]}
    async for mode, chunk in graph.astream(run.input, config,
                                           stream_mode=["updates","messages","custom"]):
        yield sse(event=mode, data=serialize(chunk))
    state = await graph.aget_state(config)
    if state.next and state.interrupts:                       # HITL pause
        yield sse(event="interrupt", data=serialize(state.interrupts))
    else:
        yield sse(event="done", data=summarize(run))
```
- **Resume HITL:** `POST /runs/:id/resume {decision, value}` -> `graph.ainvoke(Command(resume=value), config)` then re-stream.
- **Durability:** crash recovery via checkpointer; `runs.status` reflects `interrupted`.
- **Long/parallel work:** `parallel_fanout` -> `Send[]`; async subagents run in arq, posting progress to the run's SSE channel via Redis pub/sub.
- **Modes mapping for the UI:** `messages` -> token stream bubbles; `updates` -> node-by-node progress in graph view; `custom` -> tool progress / `emit_event`.

---

## 17. MCP: consuming and exposing

**Consume:** `mcp_clients` rows feed a per-project `MultiServerMCPClient`; tools merged into the tool registry. A platform tool interceptor injects `runtime.context` (per-user creds) and enforces rate limits. Headers/`auth` resolved via Auth Providers.

**Expose:** a single multi-tenant FastMCP server at `/mcp/v1/{project_id}` authenticates on `Authorization` (a project API key), loads the project's enabled tools/workflows, and registers them as MCP tools (`@mcp.tool`) and the primary workflow as a callable. Streamable-HTTP transport. This makes every project usable from Claude Desktop, Cursor, VS Code, etc.

---

## 18. Chat widget architecture

- **Loader** (`widget/loader.js`, ~3KB, on our CDN): reads `data-project-id`, `data-workflow-id`, theme attrs; injects a cross-origin `<iframe src="https://widget.forge.app/c?project=...&workflow=...&theme=...">` + a launcher button; bridges via `postMessage` (validate `event.origin` against `widget_configs.allowed_origins`).
- **Host-variable injection:** `widget_configs.host_variables` is a list like `[{name:"csrf", source:"meta", selector:"meta[name=csrf-token]", attr:"content"}, {name:"session", source:"cookie", cookie:"SESSIONID"}, {name:"user_id", source:"js", expression:"window.APP.user.id"}]`. The loader evaluates these **in host-page context**, `postMessage`s them to the iframe, which sends them to the backend as **run `context`** -> consumed by Auth Providers / tools via `ToolRuntime.context`. Advanced JS expressions are gated behind a flag + audit (self-XSS warning in UI). Only non-`HttpOnly` cookies are readable.
- **Streaming:** the iframe app opens SSE `GET /widget/:project/stream`; forwards `messages`/`custom` frames into chat bubbles; `interrupt` frames render approval UI (maps to MCP elicitation / HITL). Identity verification via HMAC (`public_key`) for authenticated deployments.
- **Theming:** CSS variables from `widget_configs.theme` (`--w-primary`, `--w-bg`, `--w-radius`, font, launcher icon/position, greeting, suggested prompts).

---

## 19. The in-product build assistant (meta-agent)

A first-class Forge workflow ("Builder Assistant") whose **tools are the Forge API itself**: `create_tool`, `create_auth_provider`, `add_node`, `connect_nodes`, `set_middleware`, `create_workflow`, `run_test`, `ingest_knowledge`. It's a `deep_agent` (planning + subagents) so it can decompose "build me a support agent for Partner Central that reads quotes" into: create auth provider -> register quote tool with projection -> create agent node with summarization + tool-call-limit -> wire router -> run a test -> report. Because the assistant uses the same API surface and full configurability, **not limiting the user also means not limiting the assistant** — exactly the user's stated reason for maximal configurability. The assistant edits the canvas live (emits node/edge mutations the frontend applies) and asks for confirmation on side-effectful steps (sending requests, publishing).

---

## 20. API surface (REST + SSE + MCP)

```
# Auth & tenancy
POST   /v1/auth/login | /v1/auth/refresh | /v1/auth/logout
GET    /v1/me

# Projects & presets
POST   /v1/projects                         GET /v1/projects                 GET/PATCH/DELETE /v1/projects/:id
GET/PUT /v1/projects/:id/config
POST   /v1/projects/:id/agents              ...                              (agent presets CRUD)

# Tools & auth
POST   /v1/projects/:id/tools               GET .../tools  GET/PATCH/DELETE .../tools/:tid
POST   /v1/projects/:id/tools/:tid/test
POST   /v1/projects/:id/auth-providers      ...  POST .../auth-providers/:aid/test
POST   /v1/projects/:id/mcp-clients         ...

# Workflows
POST   /v1/projects/:id/workflows           GET/PATCH .../workflows/:wid
PUT    /v1/projects/:id/workflows/:wid/canvas         # save canvas -> validate -> compile executable
POST   /v1/projects/:id/workflows/:wid/validate
POST   /v1/projects/:id/workflows/:wid/versions       GET .../versions   POST .../versions/:v/activate

# Runs & streaming
POST   /v1/projects/:id/workflows/:wid/runs                       -> {run_id, thread_id}
GET    /v1/projects/:id/workflows/:wid/runs/:run/stream  (SSE)
POST   /v1/projects/:id/workflows/:wid/runs/:run/resume
GET    /v1/projects/:id/threads  GET .../threads/:tid

# Knowledge & Q&A
POST   /v1/projects/:id/knowledge/sources   GET .../sources   DELETE .../sources/:sid
POST   /v1/projects/:id/qa-pairs            GET/PATCH/DELETE .../qa-pairs/:qid
POST   /v1/projects/:id/knowledge/search    (debug)

# Tracing
GET    /v1/projects/:id/traces  GET .../traces/:trid  GET .../traces/:trid/spans

# Widget (public, CORS limited by allowed_origins)
GET/PUT /v1/projects/:id/widget
POST   /v1/widget/:project/threads
POST   /v1/widget/:project/messages
GET    /v1/widget/:project/stream  (SSE)

# Project as MCP server
ALL    /mcp/v1/:project    (FastMCP, streamable-http, Authorization: project API key)

# Secrets & admin
POST   /v1/projects/:id/secrets  (write-only; never returns plaintext)
GET    /v1/admin/model-pricing  PUT /v1/admin/model-pricing
```

---

## 21. Multi-tenancy & security

- **Isolation:** every query scoped by `tenant_id`; Postgres RLS as defense-in-depth (`SET app.tenant_id` per request via middleware). Thread/checkpoint namespaces prefixed with tenant.
- **AuthZ:** roles (owner/admin/editor/viewer); project-scoped permissions; API keys scoped.
- **Instruction-source boundary:** content fetched by tools/MCP/knowledge is *data, never commands*; the agent must not act on instructions embedded in fetched content without surfacing them (prompt-injection defense baked into default system prompts + an optional guardrail middleware).
- **Egress:** all outbound tool/code traffic via per-tenant allowlist proxy.
- **Rate limits:** per-tenant + per-tool + per-widget.
- **Secrets:** §12. **Audit:** every secret read, publish, settings change, and destructive op.

---

## 22. Repository structure

```
forge/
  apps/
    api/                      # FastAPI
      forge/
        main.py routers/ services/ models/ schemas/ deps.py
        engine/               compiler.py registry.py middleware_compiler.py state.py cache.py
        nodes/                agent_node.py deep_agent_node.py tool_call.py router.py retrieval.py
                              human_input.py code.py transform.py subworkflow.py fanout.py ...
        tools/                materialize.py rest.py graphql.py mcp.py code.py builtin.py projection.py
        auth_providers/       resolver.py kinds.py extract.py inject.py
        secrets/              store.py fernet.py vault.py
        sandbox/              base.py subprocess.py docker.py remote.py
        knowledge/            embedding_store.py pgvector.py qdrant.py ingest.py qa.py search.py
        tracing/              tracer.py sink.py pricing.py
        mcp_server/           server.py            # FastMCP multi-tenant
        widget/               routes.py sse.py
        assistant/            builder_assistant.py # the meta-agent workflow + API tools
      workers/                arq_worker.py jobs/
      alembic/  tests/  pyproject.toml
    web/                      # Next.js
      app/ components/ canvas/ (React Flow) panels/ playground/ traces/ widget-config/
      lib/ hooks/ stores/ package.json
    widget/                   # embeddable
      loader.js  iframe-app/  (small React app)
  infra/                      docker-compose.yml  Dockerfiles  migrations  seed/
  docs/                       1-research.md 2-technical-design.md 3-ui-design.md
```

---

## 23. End-to-end worked example (the user's actual scenario)

**Goal:** a support agent for "Partner Central" that answers using a `getQuoteDetails` API and a knowledge base, with token-cost control and human approval before any write.

1. **Auth Provider** `ap_pc_session` (csrf_session): token-fetch POST to PC login; extract `X-CSRF-Token` header + `SESSIONID` cookie; inject both downstream; TTL 30m; refresh on 401/403. The widget pulls a fresh CSRF from the host page's `meta[name=csrf-token]` into run context for per-user calls.
2. **Tool** `tool_get_quote` (rest_api): GET `/api/quotes/{quote_id}`; only `quote_id` is `llm_visible`; response projection `data.{subtotal: totals.subtotal, total: totals.grand_total, status: status}` so the model sees ~3 fields, not the 4KB payload.
3. **Knowledge:** ingest PC help docs (PDF + URLs); add `qa_pairs` of common error->workaround.
4. **Workflow:** `start -> qa_lookup (deflect FAQs) -> router(intent) -> {billing_agent, tech_agent} -> end`. `billing_agent` is an `agent` node, model `claude-sonnet-4-6`, tools `[tool_get_quote, retrieval]`, middleware: `summarization(trigger tokens 4000)`, `context_editing(ClearToolUsesEdit keep 3)`, `tool_call_limit(tool_get_quote run 3)`, `human_in_the_loop(interrupt_on submit_change: approve/edit/reject)`, `pii(email redact)`.
5. **Test** in the playground (graph + chat modes); use `tool_emulator` to dry-run without hitting PC.
6. **Ship:** embed the widget on Partner Central (theme + allowed origin + host CSRF variable), and/or expose the project at `/mcp/v1/<project>` so internal tools can call it.

When the client later asks for the same on "1View," the user clones the project, swaps the Auth Provider + tool base URLs, and re-themes the widget — **no new servers, no new code.** That is the entire thesis of the product.

---

## 24. Testing strategy

- **Unit:** compiler (JSON->graph), `build_state_typeddict`, middleware compiler (each builder), `project_response` projection, Auth Resolver (extract/inject/refresh), tool materialization.
- **Golden workflows:** a library of executable JSON fixtures that must compile + run against mocked tools.
- **Contract:** Auth Provider/tool `/test` endpoints against a stub target server (CSRF + session simulation).
- **Security:** RLS fuzzing with cross-tenant IDs; sandbox escape attempts; secret-leak scans on logs/responses; prompt-injection corpus against default guardrails.
- **E2E:** Playwright — build a workflow on the canvas, run it, resume an interrupt, view the trace, configure + load the widget.

---

## 25. Phased build plan

Each phase is independently shippable and has acceptance criteria. Build one phase at a time. **Before each phase, re-verify LangChain/LangGraph versions and read the relevant docs page.**

### Phase 0 — Foundations (infra + skeletons)
**Scope:** docker-compose (Postgres+pgvector, Redis); FastAPI skeleton; Next.js skeleton (shadcn/ui shell, sidebar, project picker); auth (email/password + JWT); tenants/users/projects tables + Alembic; RLS middleware; pin `langchain`/`langgraph`/`deepagents`; `resolve_model` working for OpenAI + Anthropic; health/version endpoint.
**Acceptance:** sign up, create a project, call a `/v1/projects` CRUD, invoke a trivial `create_agent("...")` from a script through the service layer.

### Phase 1 — Tracer + minimal compiler + run/stream
**Scope:** `traces`/`spans` tables; `ForgeTracer` + Redis sink + flush worker; `model_pricing.json`; Node Type Registry; `compile_workflow` + `build_state_typeddict`; node types `start`, `end`, `agent` (basic, no middleware yet); `runs`/`threads`; SSE `stream_run`; `AsyncPostgresSaver` + `PostgresStore` wired; compiler cache.
**Acceptance:** create a 1-agent workflow via API, run it, watch tokens stream over SSE, see a trace with spans + cost.

### Phase 2 — Tools + Auth Providers + Secrets
**Scope:** `tools`/`auth_providers`/`secrets` tables; Fernet secret store; tool materialization (`rest_api`, `graphql`, `code` stub, `builtin`); `project_response` (JMESPath + field list); Auth Resolver (csrf_session, oauth2_client_credentials, bearer/basic/api_key) + Redis token cache + 401/403 refresh; `/tools/:id/test` and `/auth-providers/:id/test`; attach tools to the `agent` node.
**Acceptance:** register the `getQuoteDetails` tool against a stub target needing CSRF+session, test it, see raw vs projected payload + token delta, and have an agent call it successfully end to end.

### Phase 3 — Middleware compiler + Deep Agent node + node catalog
**Scope:** `middleware_compiler.py` with all prebuilt builders + the custom/advanced builders (dynamic model, tool filter, guardrail, budget); `deep_agent` node (`create_deep_agent`, subagents, filesystem backend); node types `llm`, `tool_call`, `router`, `transform`, `subworkflow`, `parallel_fanout`/`join`, `human_input` (interrupt), `code` (SubprocessSandbox + Docker), `loop`, `webhook_out`, `emit_event`; HITL resume endpoint.
**Acceptance:** build a multi-node workflow (router -> two agents, one deep_agent with a subagent) entirely via API, attach summarization + tool-call-limit + HITL, run it, pause on an interrupt, resume, and complete.

### Phase 4 — Visual builder (frontend)
**Scope:** React Flow v12 canvas; custom node components for every registry type; typed handles + `isValidConnection` + validation surfacing; palette generated from registry; node inspector = JSON-schema-driven config forms (Doc 3); canvas autosave -> `PUT /canvas` -> validate -> compile executable; version history UI; the **test playground** (graph mode + chat mode) consuming SSE; trace explorer UI.
**Acceptance:** a non-engineer can drag nodes, configure an agent's middleware via forms, connect them, save, run in the playground, and read the trace — no code.

### Phase 5 — RAG + Q&A
**Scope:** `kb_*`/`qa_pairs` tables; `PgVectorStore`; ingestion arq jobs (file/url/s3 loaders, splitters, batch embeddings, status); hybrid search; `retrieval` + `qa_lookup` nodes; knowledge + Q&A management UI; embedding model selection per project.
**Acceptance:** upload docs + add Q&A pairs, then an agent answers grounded in them with citations, and a known FAQ is deflected by `qa_lookup` before the model runs.

### Phase 6 — Chat widget + MCP server exposure
**Scope:** `widget_configs`; `loader.js` + iframe app; `postMessage` bridge + host-variable injection (incl. CSRF) -> run context; widget SSE; theming + identity HMAC; widget configurator UI with live preview; multi-tenant FastMCP server at `/mcp/v1/:project`; project API keys.
**Acceptance:** embed the widget on a test host page, have it pull a CSRF token from the page and successfully call the authenticated tool; connect the same project to Claude Desktop as an MCP server and call a tool.

### Phase 7 — Build assistant + hardening
**Scope:** the meta-agent "Builder Assistant" (deep_agent whose tools are the Forge API) with live canvas mutation + confirmations; rate limiting; audit log; RLS fuzz tests; secret-leak scans; prompt-injection guardrail defaults; remote sandbox backend (E2B/Modal/Daytona) behind a paid-tier flag; OTEL export to optional self-hosted Langfuse; docs polish.
**Acceptance:** ask the assistant "build a support agent for Partner Central that reads quotes and needs approval before writes," watch it assemble the auth provider, tool, agent, middleware, and workflow on the canvas, run a test, and report — then ship it as a widget.

---

### Build order note for Claude Code
Implement strictly Phase 0 -> 7. Within a phase, build backend abstraction + tests before the matching UI. Never introduce a dependency on `langgraph-api` or LangSmith. Keep every new behavior expressible as config (node type, middleware builder, tool kind, auth kind) so the build assistant and the user gain the capability simultaneously.
