# Forge Roadmap & Status

Forge implements the original product design end to end. This page tracks what's
**shipped**, what's **in progress**, and what's **planned** next. Contributions toward any
planned item are welcome - see [Contributing](../README.md#contributing).

## Shipped

### Core engine
- Visual workflow compiler on **LangGraph v1** - graphs compiled from a canvas into an executable, with typed state and reducers.
- Full node catalog: start/end, router, loop, parallel fan-out/join, subworkflow, transform, agent, deep agent, LLM, classifier, tool call, retrieval, Q&A lookup, human input, human handoff, webhook out, emit event.
- Provider-agnostic models (OpenAI, Anthropic, Google, or any LangChain provider) plus an offline `fake:` model for cost-free building.
- Reorderable middleware stack (prompt caching, **semantic response cache**, moderation, summarization, guardrails, model fallback, per-tenant budgets, and more).
- **Run cancellation** and graceful mid-stream error frames.
- **Durable run streaming**: execution is detached from the SSE connection, so a client disconnect leaves the run going; reconnect with `Last-Event-ID` to replay missed frames and reattach to a still-running run.

### Visual builder
- React Flow canvas with typed handles, connection validation, minimap, node inspector, save/validate, and a live-run overlay that lights up nodes over SSE.
- Canvas editing quality-of-life: unsaved-changes guard, undo/redo, and copy/paste.
- **Entity version history** - every save snapshots; view + restore across workflows, agents, tools, components, auth providers, knowledge sources, and projects, pruned to a configurable retention limit.
- Agent builder with a "what the model sees" panel (compiled prompt + middleware execution order).
- Playground chat wired to the live run stream with token and cost metering, plus Stop and thread reset.
- Light/dark theme, command palette, and the in-product Forge Assistant.

### Tools & integrations
- Tool kinds: **REST, GraphQL, Code (sandboxed), SQL, MCP**, and built-ins (calculator, time, web fetch/search, knowledge search, long-term `remember`/`recall` memory).
- **JMESPath response projection** with a raw-to-projected token meter.
- Reliability: retries with backoff, rate limits, and response caching; an **SSRF guard** on every outbound call.
- Auth Providers: Bearer, API key, Basic, OAuth2 (client-credentials and 3-legged with auto-refresh), and CSRF/session - backed by encrypted, reference-only secrets.
- **Tool sets**: many-to-many groups that organize the Tools screen, are granted to an agent in one click, and publish as MCP toolsets.
- **Per-user connected credentials**: an OAuth provider can key its token bundle per end user (via the connections API), so an agent acts as the acting user - with no token passthrough.

### Knowledge & RAG
- Ingestion from pasted text, URLs, site crawls, or file uploads; recursive / section / sentence / semantic chunking; folders.
- **Local open-source embedder (fastembed) by default** - free and offline; provider embedders optional.
- Vectors in Chroma (embedded, zero-infra) **or pgvector** (Postgres-backed, shared across workers) - selected by the `vector_backend` setting behind one interface; curated Q&A pairs with deflection; a search debugger; and re-embed health checks.
- Grounded answers: calibrated relevance floor, hybrid cosine thresholding + rerank floor, chunk citations, crawl provenance, and MMR diversity.

### Deploy
- Channels: **Email**.
- Triggers: webhook, schedule (interval/cron), inbound email, and polling app-events.
- An **embeddable web widget** (origin-locked), the run API, and a per-project **MCP server** over native **Streamable-HTTP/SSE** (clients connect directly - no bridge), authenticated by a project key, per-user **personal access token**, or optional **OAuth 2.1** - exposing **tool sets**, knowledge, Q&A, or an entire **workflow as a single tool**, with per-user identity; plus consumption of external MCP servers and a least-privileged **connector** role.
- **Import / export**: tools, workflows, agents, and components as portable `forge.bundle/1` JSON files (secret values never leave; imports never overwrite).
- Human-in-the-loop: approval pauses and live handoff to an Agent inbox.

### Observability & quality
- Per-run **span waterfall** with tokens, latency, and cost; OpenTelemetry / Langfuse export; retriever + embedding spans.
- **Tool-I/O redaction** on by default in production traces.
- **Evaluations**: datasets scored by `contains` / `exact` / `regex` / LLM-`judge`, with persisted run history and a regression gate.

### Security & operations
- Email/password auth with JWT; **refresh-token rotation, logout, password reset + email verification, and optional TOTP MFA**; login rate-limiting.
- Roles (owner/admin/editor/viewer/connector) plus **per-project RBAC** and **scoped, revocable API keys**; an audit log with pagination + export; per-tenant isolation (row-level security on Postgres).
- **Project budgets** (USD / token caps) and **allowed-model** enforcement; scheduled **data-retention purge**.
- **Guardrails & Egress policy** (project-level, admin-gated): PII redaction, blocked terms, and a network allow/deny egress list enforced on every agent by default; a project can only tighten the inherited egress policy.
- A production hardening guard that refuses to boot with unsafe defaults; a worker dead-letter queue.
- Zero-infra local stack (SQLite + embedded Chroma + in-process scheduler) that swaps - config-only - to Postgres + pgvector, Redis + an arq worker, and a durable checkpointer. Docker Compose stack included.

## In progress / next
- pgvector **ANN indexing** (HNSW per embedding dim) - the shared store ships with exact search today.
- Cross-worker durable streaming: today's reconnect/reattach is in-process; a multi-worker deployment relies on the DB run status + stale-run reaper.
- Canvas polish: auto-layout ("Tidy") and drag-from-palette.
- Remote / Docker-isolated code sandbox (a subprocess sandbox ships today).
- MCP server: stateful sessions + server-push SSE, and a formal security sign-off on the (default-off) OAuth 2.1 authorization server before internet-facing use.
- Correctness follow-ups: retry exception-name mapping, tenant-budget `jump_to:"end"` semantics, and true guardrail message replacement.

## Planned / exploring
Where the project is headed. Ideas and pull requests welcome.

- **Connectors** - a library of prebuilt, **one-click sign-in (OAuth)** integrations (Google, Slack, Notion, GitHub, HubSpot, Salesforce, and more) so adding a service is one click instead of hand-configuring a REST tool plus an auth provider.
- **More channels** - Slack, WhatsApp, Discord, and a standalone hosted web chat.
- **Template & connector marketplace** - a hosted place to browse and one-click install workflows, agents, components, and connectors (file-based **import/export** ships today).
- **Multi-modal** - image, file, and voice input/output across agents and channels.
- **Team collaboration** - multi-user canvas editing, comments, and workflow versioning with diffs.
- **Governance & SSO** - SAML/OIDC single sign-on, SCIM provisioning, and finer-grained RBAC.
- **Managed & one-click deploy** - Helm chart / Kubernetes manifests and a hosted option.
- **Proactive agents** - long-running background and scheduled agents with richer Deep Agent tooling.
- **Cost & routing** - budget alerts and automatic model routing for cost and latency.
