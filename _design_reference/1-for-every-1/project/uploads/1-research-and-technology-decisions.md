# AI Agent Platform — Research & Technology Decisions
**Version:** 1.0 · **Date:** June 2026 · **Status:** Foundational reference for Claude Code & Claude Design

> This is document 1 of 3. It records *what is true about the technology* and *why we chose what we chose*. Document 2 is the technical design + implementation plan. Document 3 is the UI design spec. When any fact here conflicts with a newer official LangChain doc, the official doc wins — re-verify versions before each build phase.

---

## 1. What we are building (one paragraph)

A multi-tenant SaaS web application where any user can create a **Project**, then visually build, configure, test, and operate AI **agents** and node/graph **workflows** (n8n-style) on top of LangChain v1 + LangGraph v1. Users register **Tools** (REST/GraphQL APIs, code, or external MCP servers) with per-field input/output schemas and response projection to control token cost; define **Auth Providers** that fetch and inject CSRF/session/OAuth credentials when calling a customer's target app; attach a stack of **middleware** for fine-grained agent behavior; store **knowledge** (RAG) and **Q&A pairs**; and then ship the result as an embeddable **chat widget** or as an **MCP server**. The platform itself hosts an assistant that can build agents/workflows for the user. Self-hosted, cost-conscious, no LangSmith dependency.

---

## 2. Current stack reality (June 2026) — corrected

| Component | Version (Jun 2026) | License | Notes |
|---|---|---|---|
| `langchain` | **1.3.x** (1.3.4, Jun 2 2026) | MIT | Stable; no breaking changes promised until 2.0. Requires Python >= 3.10. |
| `langgraph` | **1.2.x** (1.2.4, Jun 2 2026) | MIT | Orchestration runtime. Minor releases monthly, patches ~weekly. |
| `langchain-core` | 1.3.x | MIT | |
| `deepagents` | **0.5.x+** | MIT | Agent harness: planning, subagents, filesystem, sandboxes, skills. |
| `langgraph-checkpoint-postgres` | 3.x | MIT | `AsyncPostgresSaver`. Our durable execution. |
| `langchain-mcp-adapters` | 0.2.x+ | MIT | MCP client (`MultiServerMCPClient`) + interceptors. |
| `fastmcp` | 3.x | Apache-2.0 | Build/expose MCP servers. |
| **`langgraph-api`** | — | **Elastic 2.0 (!)** | The server that `langgraph dev/up/build` launches. **Avoid in our default path** — needs a `LANGGRAPH_CLOUD_LICENSE_KEY` for production. |
| LangSmith / LangSmith Deployment | hosted | Commercial (!) | Tracing + deploy + "Studio". **Not used.** Per-run + per-standby-minute pricing. |

**Semver promise:** upgrading 1.0 -> 1.1 -> 1.2 -> 1.3 is non-breaking. Pin `langchain>=1.3,<2`, `langgraph>=1.2,<2`, `deepagents>=0.5,<1`.

**Model names current in the docs** (use these in examples, they change often): `openai:gpt-5.4`, `gpt-5.4-mini`, `anthropic:claude-sonnet-4-6`, `google_genai:gemini-3.1-pro-preview`, `gemini-3.5-flash`. Treat all model strings as configuration, never hardcode.

### The licensing line we never cross
`langgraph` (framework), `langgraph-cli`, `langgraph-sdk`, `langgraph-checkpoint-*` are MIT and free. The **server runtime** (`langgraph-api`) and **LangSmith** are not. We build our **own FastAPI server** on the MIT framework. The open-source project **Aegra** (Apache-2.0) is proof this works and a reference implementation — a FastAPI + Postgres + Agent-Protocol drop-in for LangSmith Deployment.

---

## 3. LangChain v1 — the agent + middleware model

### 3.1 `create_agent` is the core primitive
`from langchain.agents import create_agent`. It builds a **LangGraph graph** under the hood (model node <-> tools node <-> middleware), runs a ReAct tool loop until the model emits a final answer or a limit is hit. Signature essentials: `model`, `tools`, `system_prompt` (str or `SystemMessage`), `response_format`, `state_schema`, `context_schema`, `checkpointer`, `store`, `middleware`, `name`.

- **Static model:** `create_agent("openai:gpt-5.4", tools=...)` or pass a configured `ChatOpenAI(...)` instance.
- **Multi-model / dynamic model:** done via middleware (`@wrap_model_call` -> `request.override(model=...)`), e.g. cheaper model for short conversations, advanced model after N messages. **This is how "each agent uses a different model" and "route by complexity" are implemented.**
- **Structured output:** `response_format=PydanticModel` -> defaults to `ProviderStrategy` (native) when supported, falls back to `ToolStrategy` (artificial tool call). Both selectable explicitly.
- **State is `TypedDict` only.** As of v1, agent/graph custom state schemas **must** subclass `AgentState` (a `TypedDict`). **Pydantic models and dataclasses are no longer accepted for state.** (Tool *argument* schemas are still Pydantic — different thing.)
- **Names:** snake_case for agent and tool names; some providers reject spaces/special chars.

### 3.2 Middleware is the configurability engine
Everything fine-grained is middleware. Hooks (sequential in, reverse-sequential out — the web-server "onion"):

| Hook | Fires | Use |
|---|---|---|
| `@before_model` | before each model call | trim/inject context, set state |
| `@wrap_model_call` | around model call | dynamic model, dynamic tools (filter), guardrails |
| `@after_model` | after model response | validate/modify output, content filtering |
| `@wrap_tool_call` | around each tool call | error handling, retries, dynamic-tool execution, request signing |
| `AgentMiddleware` (class) | any/all + `state_schema` + `tools` | bundle custom state + tools + multiple hooks |

### 3.3 Prebuilt middleware catalog (assemble, don't build)
All `from langchain.agents.middleware import ...`. Each maps to a config block in our UI.

| Middleware | Key params | What it gives the user |
|---|---|---|
| `SummarizationMiddleware` | `model`, `trigger=(("tokens"|"messages"|"fraction"), v)` or list (OR), `keep`, `summary_prompt` | Auto-summarize history near token limits |
| `HumanInTheLoopMiddleware` | `interrupt_on={tool: {allowed_decisions:[approve,edit,reject]}}` | Pause for approval on sensitive tools (needs checkpointer) |
| `ModelCallLimitMiddleware` | `thread_limit`, `run_limit`, `exit_behavior=end|error` | Cost ceiling on model calls |
| `ToolCallLimitMiddleware` | `tool_name?`, `thread_limit`, `run_limit`, `exit_behavior=continue|error|end` | Global or per-tool call caps |
| `ModelFallbackMiddleware` | `*models` (ordered) | Failover across providers |
| `PIIMiddleware` | `pii_type`, `strategy=block|redact|mask|hash`, `detector`, `apply_to_input/output/tool_results` | PII handling; custom detectors via regex/fn |
| `TodoListMiddleware` | `system_prompt?` | Adds `write_todos` planning tool |
| `LLMToolSelectorMiddleware` | `model`, `max_tools`, `always_include` | Pre-select relevant tools when 10+ exist (token saver) |
| `ToolRetryMiddleware` | `max_retries`, `backoff_factor`, `initial_delay`, `max_delay`, `jitter`, `tools?`, `retry_on`, `on_failure` | Resilient tools |
| `ModelRetryMiddleware` | same retry family; `on_failure=continue|error|fn` | Resilient model calls |
| `LLMToolEmulator` | `tools?`, `model` | **Emulate tools with an LLM for testing** — ideal for our test panel "dry run" |
| `ContextEditingMiddleware` + `ClearToolUsesEdit` | `trigger`, `keep`, `clear_tool_inputs`, `exclude_tools` | Clear old tool outputs at token threshold (token saver) |
| `ShellToolMiddleware` | `workspace_root`, `execution_policy = HostExecutionPolicy | DockerExecutionPolicy | CodexSandboxExecutionPolicy`, `startup_commands`, `redaction_rules` | Persistent shell with built-in Docker/Codex isolation |
| `FilesystemFileSearchMiddleware` | `root_path`, `use_ripgrep` | Glob/Grep over files |
| `FilesystemMiddleware` (deepagents) | `backend`, `custom_tool_descriptions` | ls/read/write/edit file tools; pluggable backend |
| `SubAgentMiddleware` (deepagents) | `default_model`, `subagents=[{name,description,system_prompt,tools,model,middleware}]` | `task` tool to spawn isolated subagents; also `CompiledSubAgent(runnable=<any compiled graph>)` |

Provider-specific: Anthropic (prompt caching, bash, text editor, memory, file search), AWS Bedrock (prompt caching), OpenAI (moderation).

**Three independent token-cost levers** (all relevant to the user's obsession): (a) per-tool **response projection** (our JMESPath layer), (b) **`ContextEditingMiddleware`/`ClearToolUsesEdit`**, (c) **`SummarizationMiddleware`** + **filesystem offload** of large results.

---

## 4. LangGraph v1 — the runtime

- **`StateGraph(StateSchema)`** with `add_node`, `add_edge`, `add_conditional_edges`, `START`/`END`, `.compile(checkpointer=, store=, interrupt_before=, interrupt_after=)`.
- **State + reducers:** annotate fields (`Annotated[list, add_messages]`, `Annotated[list, operator.add]`) so parallel writes merge.
- **`Send` API:** a conditional edge returns `[Send("node", substate), ...]` for runtime fan-out / map-reduce.
- **`Command`:** a node returns `Command(update={...}, goto="next")` to update state **and** route in one step (handoffs).
- **Durable execution:** checkpointers persist state at every superstep; a run resumes from the last checkpoint after a crash. Production = `AsyncPostgresSaver` over a `psycopg` async pool. Encrypted state via `EncryptedSerializer`.
- **Threads:** every invocation carries `config={"configurable": {"thread_id": ..., "checkpoint_ns": ...}}`. Prefix with tenant.
- **HITL:** `from langgraph.types import interrupt, Command`. A node calls `interrupt(payload)` -> run pauses -> emits `{'__interrupt__': (...)}` -> resume with `graph.invoke(Command(resume=value), config)` on the same thread. **Node re-executes from the top on resume** — put side effects after the interrupt. Checkpointer mandatory. (`HumanInTheLoopMiddleware` wraps this for tool approval.)
- **Streaming modes** on `astream(..., stream_mode=[...])`: `values` (full state), `updates` (per-node deltas), `messages` (LLM token stream -> chat UI), `debug` (superstep internals), `custom` (anything a node writes via `StreamWriter`).
- **Subgraphs:** add a compiled graph as a node; shared state keys merge.
- **Store (long-term memory):** `BaseStore` with namespace/key (`store.get((ns,), key)`, `store.put(...)`). Production = `PostgresStore`. Survives across threads/sessions. Deep Agents routes `/memories/` filesystem paths to a `StoreBackend` for persistent agent memory.
- **Multi-agent patterns:** supervisor (central router via handoff tools — LangChain now recommends building handoffs manually for context control), swarm (peer handoffs, active-agent tracked), hierarchical (supervisor of supervisors). `langgraph-supervisor`/`langgraph-swarm` exist as MIT prebuilts; `SubAgentMiddleware` + `CompiledSubAgent` is the simplest path and lets **any compiled workflow become a subagent of another** — our "projects calling projects" composition primitive.

---

## 5. Deep Agents — the harness for complex tasks

`from deepagents import create_deep_agent` (same call shape as `create_agent`, plus harness features). Built on LangChain core + LangGraph runtime. Ships:

- **Planning** — `write_todos` tool, plan-before-acting prompts.
- **Context management** — compresses history, offloads large tool inputs/results to a **virtual filesystem**, summarizes older messages -> keeps long runs effective (token saver).
- **Pluggable filesystem backends** — `StateBackend` (in graph state, ephemeral), local disk, `StoreBackend` (persistent), `CompositeBackend(default=..., routes={"/memories/": StoreBackend()})`, or custom with **permission rules** (read/write).
- **Shell execution** — `LocalShellBackend` (host) or a **sandbox backend** for isolation.
- **Interpreters** — run JavaScript (QuickJS) in-memory to compose tools / transform data without a full shell.
- **Subagents** — `task` tool spawns general or named specialized subagents in isolated context windows; **async subagents** run in background with progress checks, follow-ups, cancellation.
- **Long-term memory, filesystem permissions, HITL, skills, smart defaults.**

**Sandbox backends** (`backend=` on `create_deep_agent`): `DaytonaSandbox` (`langchain-daytona`), `ModalSandbox` (`langchain-modal`), `Runloop`, `AgentCore` (AWS Bedrock AgentCore), `E2B` (`langchain-e2b`, partner package). Two connection patterns: **agent-in-sandbox** (mirrors local dev; tight coupling) vs **sandbox-as-a-tool** (agent outside, sandbox is a tool; *API keys stay on our side* — preferred for multi-tenant). `ShellToolMiddleware` additionally offers `DockerExecutionPolicy` / `CodexSandboxExecutionPolicy` without a remote provider.

**Platform decision:** expose **two agent node flavors** — `agent` (`create_agent`, ReAct loop) for most cases, `deep_agent` (`create_deep_agent`) for autonomous multi-step "NASA-tier" work. The Deep Agent flavor exposes planning/subagents/filesystem/sandbox toggles. We do **not** hand-roll any of these.

---

## 6. Tools & `ToolRuntime`

- `@tool` decorator; type hints required; docstring -> description; override name/description; `args_schema` accepts a **Pydantic model OR a JSON Schema** (our config emits one of these).
- **`StructuredTool.from_function(func|coroutine, name, description, args_schema)`** for dynamic/runtime tools.
- **`ToolRuntime`** parameter (auto-injected, hidden from the LLM; cannot name your own args `runtime` or `config`) exposes: **State** (short-term), **Context** (immutable per-invocation config — *user IDs, session, CSRF passed by the widget*), **Store** (long-term), **Stream Writer** (`runtime.stream_writer(...)` -> progress to widget), **Execution Info** (thread_id, run_id, node_attempt — needs deepagents>=0.5 / langgraph>=1.1.5), **Server Info**, **Config**, **Tool Call ID**.
- Tools return a **string** (-> ToolMessage), an **object/dict** (structured), or a **`Command`** (update state + ToolMessage via `runtime.tool_call_id`).
- **`ToolNode`** (`langgraph.prebuilt`) executes tools with parallel execution + error handling (`handle_tool_errors=True|str|fn|tuple`); **`tools_condition`** routes by whether the model emitted tool calls. These power our `tool_call` node type.

**Implication for our REST-API tool:** the materialized callable takes the LLM-visible args plus `runtime: ToolRuntime`; inside it reads `runtime.context` for the per-user auth context (CSRF/session the widget injected), calls the Auth Resolver, executes the HTTP request, applies field projection/JMESPath, optionally emits `runtime.stream_writer` progress, and returns a dict.

---

## 7. MCP — consume *and* expose

**Consume external MCP servers** (`langchain-mcp-adapters`):
```python
client = MultiServerMCPClient({
  "billing": {"transport": "http", "url": "https://portal/mcp",
              "headers": {"Authorization": "Bearer ..."}},   # or "auth": httpx.Auth (OAuth2)
  "local":   {"transport": "stdio", "command": "python", "args": ["server.py"]},
})
tools = await client.get_tools()      # also get_resources(), get_prompt()
```
- **Stateless by default** (fresh session per call); `async with client.session("name")` for stateful servers.
- **Tool interceptors** (`tool_interceptors=[...]`) — onion-ordered async `(request, handler)` functions that **bridge MCP (separate process) to LangGraph runtime**: read `request.runtime.context` (user_id/api_key), `runtime.store`, `runtime.state`; `request.override(args=..., headers=...)`; return `ToolMessage` to short-circuit or `Command` to update state/route. **This is the mechanism to inject per-user auth and rate-limit MCP tool calls.**
- Structured content -> `MCPToolArtifact` on `ToolMessage.artifact`; multimodal -> `content_blocks`. Progress + logging + **elicitation** callbacks (server can request more user input mid-call — maps to widget HITL).

**Expose a project as an MCP server** (FastMCP):
```python
mcp = FastMCP("ProjectXYZ")
@mcp.tool()
def add(a: int, b: int) -> int: ...
mcp.run(transport="http")  # or "stdio"
```
Production: one multi-tenant MCP server authenticating on `Authorization` and dispatching to the right project's tools.

---

## 8. Dynamic config -> executable graph

The proven pattern (Langflow): store a flow as JSON `nodes[] + edges[]`; a builder topologically sorts and instantiates each node. We replicate with a **Node Type Registry** mapping `node.type -> factory(config) -> node_callable`, and a `compile_workflow(definition) -> CompiledStateGraph`. Keep **canvas JSON** (React Flow round-trip, includes positions) **separate** from **executable JSON** (compiler input); translate canvas -> executable on save with validation. State schema is built as a **`TypedDict`** at runtime (not Pydantic). Tool arg schemas are built as **Pydantic/JSON Schema**.

---

## 9. Visual builder frontend

**React Flow v12 (`@xyflow/react`)** — the canvas behind Langflow/Flowise/n8n-likes. Primitives: `<ReactFlow nodes edges onNodesChange onEdgesChange onConnect nodeTypes>`, `useNodesState`/`useEdgesState`/`addEdge`, `<Handle type="source|target" position id>` (unique handle ids for multi-port), `isValidConnection`, `onBeforeDelete`, `MiniMap`/`Controls`/`Background`, `useUpdateNodeInternals()`. Encode an `IOType` enum on handles and enforce in `isValidConnection`; color-code by type. `dagre` for auto-layout. Don't roll your own canvas — ~2-week penalty.

---

## 10. Tracing without LangSmith

Every Runnable accepts `config={"callbacks": [handler]}`. Implement a `BaseCallbackHandler` (or use `astream_events(version="v2")`) capturing `on_chat_model_start/end`, `on_llm_new_token`, `on_tool_start/end`, `on_chain_start/end/error` (LangGraph emits per node), `on_retriever_*`. Read token usage from `AIMessage.usage_metadata`. Persist `traces` + `spans` to Postgres (schema in Doc 2). Batch through Redis -> worker. Maintain `model_pricing.json` for cost. Standardize span attributes on **OpenTelemetry GenAI semantic conventions** (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, ...) so we can later plug in **Langfuse** (MIT, self-hostable, OTEL-native) as an opt-in without rework. Feature idea to steal long-term: LangSmith's "Engine" auto-detects trace issues and proposes fixes.

---

## 11. Target-app auth injection (industry patterns)

Reference models: Postman/Insomnia pre-request scripts; **n8n credentials** (encrypted in DB, `{{$secrets...}}` references, external-secrets defer to Vault/AWS SM/Azure KV/GCP SM, resolved only at execution); Zapier declarative auth (OAuth2/API-key/session/digest); OAuth2 client-credentials; cookie-jar session brokers; OWASP CSRF (synchronizer token / double-submit cookie — we implement the *client* side: fetch the token, replay it in header + cookie the target expects).

**Our model — "Auth Provider" per project** describing: a token-fetch HTTP recipe, **extraction rules** (from header / `Set-Cookie` / JSON path, with a TTL field), **injection rules** (to header / cookie / query for downstream tool calls), and an encrypted **credentials reference**. An **Auth Resolver** caches tokens per `(tenant, provider, context-hash)` with TTL, invalidates on 401/403, and follows cookie-jar semantics. Per-user secrets (CSRF/session that the widget pulls from the host page) arrive at call time via `ToolRuntime.context`, never stored. Full spec in Doc 2.

---

## 12. Sandboxing — decision

| Tier | Mechanism | When |
|---|---|---|
| MVP / free | subprocess + `setrlimit` (CPU/AS/NOFILE/NPROC) + 30s timeout + egress allowlist proxy + `RestrictedPython` for expression eval; `ShellToolMiddleware(DockerExecutionPolicy)` for code nodes | Trusted users; fast to ship |
| Paid / enterprise | Remote sandbox backend — **Daytona / Modal / E2B / Runloop / AgentCore** via Deep Agents' backend protocol, **sandbox-as-a-tool** pattern (API keys stay on our side) | Arbitrary user code, regulated/enterprise tenants, parallel remote execution |

Define a single `Sandbox` interface now (`run(code, inputs, limits) -> result`); implement `SubprocessSandbox` first, swap to a remote backend by config. **Subprocess + rlimits is not a hard security boundary** — if signups are fully open and run arbitrary code, ship a remote sandbox from day one or gate code nodes behind a paid tier.

---

## 13. RAG & vector store — decision

Start with **pgvector** in the same Postgres: zero new infra, per-tenant row isolation + RLS, ACID, HNSW. On a published 50M-vector / 768-dim / 99%-recall benchmark on identical hardware, pgvectorscale delivered ~**471 QPS vs Qdrant ~41 QPS** (Qdrant kept lower p95 at that scale). Add **Qdrant** later only for true per-tenant shard isolation (custom shard key = tenant_id) or extreme scale. Write an `EmbeddingStore` interface (`upsert/query/delete_by_doc`) so the swap is config-level. Chunkers: `RecursiveCharacterTextSplitter` (1000/200), `MarkdownHeaderTextSplitter`. Hybrid search via Postgres `tsvector` + vector -> reciprocal rank fusion. Embeddings: default `text-embedding-3-small` (1536); self-host `bge-large-en-v1.5` / `nomic-embed-text-v1.5`. Store the embedding model name on each chunk row for safe migration. **Q&A pairs** (user question + answer, error + workaround) get their own table with a question embedding for retrieval.

---

## 14. Supporting stack — decision

- **API:** FastAPI + Uvicorn, async throughout. **SSE** for streaming (`text/event-stream`), forwarding LangGraph `messages`/`updates`/`custom`.
- **DB:** Postgres 16+ with pgvector, SQLAlchemy 2 async + Alembic.
- **Cache/queue:** Redis 7+; background jobs via **arq** (skip Celery). LangGraph executions rely on its own checkpointer durability.
- **Auth (platform):** JWT/session (FastAPI-Users acceptable), Authlib for SSO.
- **Frontend:** Next.js + React + React Flow v12 + Tailwind + shadcn/ui.
- **Object storage:** S3/R2. **Secrets:** Fernet (`cryptography`) master key for MVP; Vault/OpenBao/cloud SM optional.
- **Server:** our own (don't depend on `langgraph-api`); model structure on Aegra if helpful.

---

## 15. Competitive landscape (know it, position against it)

- **LangSmith Fleet** — LangChain's own *no-code agent builder* ("templates, integrations, routine automation"). Closest thing to our product, but hosted/commercial and not self-hostable/embeddable as your own SaaS. Validates the concept. We differentiate on: self-hosting, per-field tool config + response projection, target-app auth injection, embeddable customizable widget, project-as-MCP, and an in-product build assistant.
- **LangSmith Studio** (was LangGraph Studio) — developer debugger/visualizer for code-defined graphs, coupled to LangSmith. Use it for *our own* debugging; it is **not** our customers' builder.
- **Flowise / Langflow / Dify / n8n / Activepieces** — architectural references for canvas + node registry + execution. Their runtimes hit production limits; we write a thin compiler straight onto LangGraph.

---

## 16. Decisions summary

| # | Decision | Rationale |
|---|---|---|
| 1 | Build our own FastAPI server; never depend on `langgraph-api` (Elastic 2.0) or LangSmith | Margins, self-host, no license keys |
| 2 | Middleware stack = the agent configurability engine | Maps the user's "every customization" to assembling prebuilt + custom middleware |
| 3 | Two agent flavors: `agent` and `deep_agent` | Get planning/subagents/context-mgmt/sandbox for free at the high end |
| 4 | Canvas JSON != executable JSON; compile via Node Type Registry | Clean separation; validated translation |
| 5 | State = TypedDict; tool args = Pydantic/JSON Schema | Hard v1 constraint |
| 6 | pgvector now, Qdrant interface later | Zero infra, strong QPS, easy migration |
| 7 | Self-built tracer (OTEL conventions) + Langfuse opt-in | Always-on UX, no lock-in |
| 8 | Auth Provider + Resolver; per-user secrets via `ToolRuntime.context` | Solves CSRF/session injection cleanly |
| 9 | Sandbox interface: subprocess+Docker MVP -> remote backend paid | Pragmatic 1-month build, safe upgrade path |
| 10 | Project-as-MCP via FastMCP is a first-class feature | Distribution: every project usable in Claude/Cursor/VS Code |

---

## 17. Open risks / caveats

- **v1.x is young** (1.0 Oct 2025, now 1.3). Pin minors; re-read changelog each phase. Middleware API most likely to evolve.
- **Per-tenant isolation in shared Postgres** is defense-in-depth (RLS + row predicates) but not sufficient alone — schedule isolation audits with fuzzed tenant IDs.
- **Credential blast radius:** we hold secrets that act on customers' systems. Treat the secret store as PCI-scope-equivalent — KMS, dual-control prod access, audit every read, scheduled key rotation.
- **Subprocess sandboxing is not a hard boundary.** Open signups + arbitrary code => remote sandbox from day one.
- **Vendor cold-start/QPS/pricing numbers** are directionally correct but workload-dependent — benchmark before committing budget.
- **Aegra <-> `langgraph-api` is conceptual, not literal** — validate specific endpoints if you want the official SDK/Studio to point at our server.

---

## 18. Key sources
LangChain docs (June 2026): `/oss/python/langchain/agents`, `/middleware/built-in`, `/langchain/tools`, `/langchain/mcp`, `/langgraph/overview`, `/deepagents/overview`, `/deepagents/sandboxes`, `/integrations/providers/overview`. LangChain blog: "The two patterns by which agents connect sandboxes." PyPI version pages (langchain 1.3.4, langgraph 1.2.4). Aegra (github.com/ibbybuilds/aegra). React Flow / xyflow v12 docs. Timescale pgvector-vs-Qdrant benchmark. n8n external-secrets docs, OWASP CSRF.
