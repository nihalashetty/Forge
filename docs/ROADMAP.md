# Forge — Build Roadmap & Status

Phased plan from `docs/2-technical-design-and-implementation.md` §25, annotated with
real status. Legend: ✅ done & validated · 🟡 partial · ⬜ not started.

## Deliberate deviations from the design docs (and why)

| Decision | Doc said | We do | Why |
|---|---|---|---|
| Vector store | pgvector | **Chroma** (embedded persistent) | User instruction; zero-infra local dev. `EmbeddingStore` interface keeps it swappable. |
| Local relational DB | Postgres 16 | **SQLite** (`aiosqlite`) | No Docker on dev machine. SQLAlchemy 2 async → Postgres is a URL change. |
| Run durability | `AsyncPostgresSaver` | **`AsyncSqliteSaver`** locally | Same reason; `langgraph-checkpoint-postgres` is a config swap. |
| Cache / queue | Redis + arq | **in-process** locally | No Redis on dev machine; interface preserved for prod. |
| Default theme | dark-first | **light default + dark toggle** | The exported design prototype ships light as default (handoff README: match the prototype). |
| Provider middleware | `langchain.agents.middleware` | provider packages | Validated: `AnthropicPromptCachingMiddleware`/`OpenAIModerationMiddleware` live in `langchain-anthropic`/`langchain-openai`. |

## Validated environment facts (2026-06-07)

`langchain 1.3.4`, `langchain-core 1.4.1`, `langgraph 1.2.4`, `deepagents 0.6.8`, `chromadb 1.5.9`.
`create_agent`, `AgentState`, all 5 middleware hooks, and 13/15 prebuilt middleware import exactly
as documented. `ToolRuntime` is at `langchain.tools` (not `langchain_core.tools`).
`ModelFallbackMiddleware(first_model, *additional_models)`.

---

## Phase 0 — Foundations ✅ (auth pending)
- ✅ Monorepo structure (apps/api, apps/web, packages/schemas, docs, infra)
- ✅ Dependency pins validated against PyPI; editable install
- ✅ Settings (pydantic-settings), zero-infra defaults
- ✅ `resolve_model` (OpenAI/Anthropic/gateways via init_chat_model + offline `fake:` scheme)
- ✅ Async SQLAlchemy + SQLite, ORM (tenants/users/projects/workflows/threads/runs/traces/spans), dev seed (6 projects)
- ✅ FastAPI app factory + lifespan (DB init, AsyncSqliteSaver checkpointer), CORS, health/version
- ⬜ Auth (email/password + JWT), RLS-style tenant scoping (tenant currently stubbed to seed)

## Phase 1 — Tracer + compiler + run/stream ✅
- ✅ Node Type Registry (`NodeSpec`, `Port`, IOType compatibility)
- ✅ `compile_workflow` + `build_state_typeddict` (TypedDict + reducers)
- ✅ Nodes: start, end, router, agent, deep_agent, llm
- ✅ Engine test suite (7/7): compile + routed run + middleware + projection + sandbox
- ✅ `ForgeTracer` callback (LLM/tool/chain spans, token+cost) → persisted to traces/spans per run
- ✅ `runs`/`threads` + SSE `stream_run` (updates/messages/custom) — validated live end-to-end
- ✅ Validation service (schema + structural rules) + 7/7 tests + `/validate` endpoint
- ✅ Middleware-stack compiler (all prebuilt + custom builders; advanced ones need live-call validation)

## Phase 2 — Tools + Auth Providers + Secrets ✅ (code/mcp tools pending)
- ✅ `project_response` projection (JMESPath + field list + token estimate)
- ✅ Tool materialization: rest_api, graphql, builtin (current_time/calculator/web_fetch); code/mcp stubbed
- ✅ `ToolRuntime`-aware StructuredTools wired into agents; standalone `execute_*` cores for /test
- ✅ Auth Resolver: csrf_session (fetch/extract/inject), oauth2_client_credentials, bearer, basic, api_key + in-process cache + 401/403 refresh
- ✅ Fernet secret store (`secret://proj/<name>`); `/tools/:id/test` + `/auth-providers/:id/test` (masked)
- ✅ Runtime assembler materializes a project's tools into the CompileContext for runs
- ✅ Tests (22 total): secrets roundtrip, csrf/bearer resolve, REST projection+args, builtin test, **agent→tool full loop (6*7=42)**

## Phase 3 — Full node catalog ✅ (a few advanced nodes remain)
- ✅ I/O convention: data nodes read `input_key` / write `output_key` (declared state field)
- ✅ Nodes: transform, human_input (interrupt), webhook_out, emit_event, **tool_call** (invokes
      materialized rest/graphql/builtin tools), **retrieval** + **qa_lookup** (RAG-grounded)
- ✅ **HITL resume** endpoint (`POST /runs/:id/resume` → `Command(resume=...)` on the thread)
- ⬜ code (sandbox), parallel_fanout/join, loop, subworkflow (compiler fan-out/cycle wiring)

## Phase 4 — Visual builder (frontend) 🟡
- ✅ Next.js (App Router, TS) + design tokens (verbatim) + fonts + globals
- ✅ App shell: global rail, topbar, breadcrumbs, project sidebar, ⌘K command palette, Assistant drawer, light/dark toggle
- ✅ Screens: Dashboard (live projects), Project Overview, Onboarding wizard — faithful to the handoff
- ✅ Playground (chat mode) wired to the live SSE run stream (the live-run signature)
- ✅ API client (`lib/api.ts`) + fetch-based SSE parser; Next rewrite proxies `/api/forge/*` → backend
- ✅ Tools list + **Tool Builder** with the Raw→Projected token meter — both a client-side
      JMESPath projection preview (edit sample → meter shrinks) AND the live `/tools/:id/test`
      panel (real token delta; calculator/current_time succeed offline)
- ✅ **Workflow Canvas on React Flow v12** — palette from `/v1/node-types`, custom Forge nodes,
      IOType-typed handles + connection validation, drag/pan/zoom/minimap, node inspector
      (AgentConfig / router cases / JSON fallback), Save→`PUT /canvas` (validate + problems tray), Run
- ✅ **Workflows list** + **Agents list** + **Agent config** with the middleware-stack UI
      (add/reorder/toggle cards, categorized catalog) — reused in the canvas inspector
- ✅ Backend: `agents` table + CRUD, `PUT /workflows/:id/canvas` (validate + store canvas & executable)
- ✅ **All screens now real** (no placeholders): Knowledge (sources/Q&A/search), Traces
      (span waterfall + cost), Auth Providers (templated config + masked test), Settings
      (config + budgets + feature flags + secrets),
      Connect/MCP (endpoint + client config + exposed tools)
- ✅ **Canvas live-run overlay** — Run streams the workflow over SSE and lights nodes up in order
- ⬜ dagre auto-layout ("Tidy"); drag-from-palette (click-to-add works today)

## Phase 5 — RAG + Q&A ✅
- ✅ Offline-capable embedder (hashed words + char-trigrams; provider embedder when keyed)
- ✅ `ChromaStore` EmbeddingStore (embedded persistent, tenant/project scoped)
- ✅ Ingestion (paste text / URL → strip → recursive split → embed → upsert), `kb_sources`
- ✅ `qa_pairs` (+ stored question embedding) and `qa_lookup` deflection
- ✅ Search debugger + `retrieval` node (injects top-k context); tests for splitter/embedder/ingest/qa

## Phase 6 — MCP 🟡
- ✅ Connect screen — expose this project's tools as an MCP server + register external MCP servers
- ⬜ multi-tenant FastMCP server hardening
- Note: the embeddable chat widget was removed; deploy via Channels (email/Teams), the run API, or MCP.

## Phase 7 — Build assistant + hardening ⬜
- ⬜ Build-assistant meta-agent (deep_agent whose tools are the Forge API); JWT auth (tenant stubbed);
      rate limiting; audit log; remote sandbox; Langfuse OTLP export

---

### Known follow-ups / corner cases to revisit
- `tool_retry.retry_on` (string exception names) → map to exception types before passing to the lib.
- `tenant_budget` `jump_to:"end"` semantics + reading accumulated cost from `traces`.
- `guardrail_regex` block currently *appends* a replacement message; true replacement needs RemoveMessage.
- Nested checkpointer: embedded agents intentionally carry none; top-level graph owns durability.
