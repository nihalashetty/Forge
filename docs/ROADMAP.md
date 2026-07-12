# Forge Roadmap & Status

Forge implements the original product design end to end. This page tracks what's
**shipped**, what's **in progress**, and what's **planned** next. Contributions toward any
planned item are welcome - see [Contributing](../README.md#contributing).

## Shipped

### Core engine
- Visual workflow compiler on **LangGraph v1** - graphs compiled from a canvas into an executable, with typed state and reducers.
- Full node catalog: start/end, router, loop, parallel fan-out/join, subworkflow, transform, agent, deep agent, LLM, classifier, tool call, retrieval, Q&A lookup, human input, human handoff, webhook out, emit event.
- Provider-agnostic models (OpenAI, Anthropic, Google, or any LangChain provider) plus an offline `fake:` model for cost-free building.
- Reorderable middleware stack (prompt caching, moderation, summarization, guardrails, model fallback, per-tenant budgets, and more).

### Visual builder
- React Flow canvas with typed handles, connection validation, minimap, node inspector, save/validate, and a live-run overlay that lights up nodes over SSE.
- Agent builder with a "what the model sees" panel (compiled prompt + middleware execution order).
- Playground chat wired to the live run stream with token and cost metering.
- Light/dark theme, command palette, and the in-product Forge Assistant.

### Tools & integrations
- Tool kinds: **REST, GraphQL, Code (sandboxed), SQL, MCP**, and built-ins (calculator, time, web fetch/search, knowledge search, long-term `remember`/`recall` memory).
- **JMESPath response projection** with a raw-to-projected token meter.
- Reliability: retries with backoff, rate limits, and response caching; an **SSRF guard** on every outbound call.
- Auth Providers: Bearer, API key, Basic, OAuth2 (client-credentials and 3-legged with auto-refresh), and CSRF/session - backed by encrypted, reference-only secrets.

### Knowledge & RAG
- Ingestion from pasted text, URLs, site crawls, or file uploads; recursive / section / sentence / semantic chunking; folders.
- **Local open-source embedder (fastembed) by default** - free and offline; provider embedders optional.
- Vectors in Chroma (local) or pgvector (production); curated Q&A pairs with deflection; a search debugger; and re-embed health checks.

### Deploy
- Channels: **Email** and **Microsoft Teams**.
- Triggers: webhook, schedule (interval/cron), inbound email, chat, and polling app-events.
- An **embeddable web widget** (origin-locked), the run API, and an **MCP server** to expose tools - plus consumption of external MCP servers.
- Human-in-the-loop: approval pauses and live handoff to an Agent inbox.

### Observability & quality
- Per-run **span waterfall** with tokens, latency, and cost; OpenTelemetry / Langfuse export.
- **Evaluations**: datasets scored by `contains` / `exact` / `regex` / LLM-`judge`.

### Security & operations
- Email/password auth with JWT; roles (owner/admin/editor/viewer); an audit log; per-tenant isolation (row-level security on Postgres).
- A production hardening guard that refuses to boot with unsafe defaults.
- Zero-infra local stack (SQLite + embedded Chroma + in-process scheduler) that swaps - config-only - to Postgres + pgvector, Redis + an arq worker, and a durable checkpointer. Docker Compose stack included.

## In progress / next
- Canvas polish: auto-layout ("Tidy") and drag-from-palette.
- Remote / Docker-isolated code sandbox (a subprocess sandbox ships today).
- Multi-tenant MCP server hardening.
- Correctness follow-ups: retry exception-name mapping, tenant-budget `jump_to:"end"` semantics, and true guardrail message replacement.

## Planned / exploring
Where the project is headed. Ideas and pull requests welcome.

- **Connectors** - a library of prebuilt, **one-click sign-in (OAuth)** integrations (Google, Slack, Notion, GitHub, HubSpot, Salesforce, and more) so adding a service is one click instead of hand-configuring a REST tool plus an auth provider.
- **More channels** - Slack, WhatsApp, Discord, and a standalone hosted web chat.
- **Template & connector marketplace** - share and install workflows, agents, components, and connectors.
- **Multi-modal** - image, file, and voice input/output across agents and channels.
- **Team collaboration** - multi-user canvas editing, comments, and workflow versioning with diffs.
- **Governance & SSO** - SAML/OIDC single sign-on, SCIM provisioning, finer-grained RBAC, and PII redaction.
- **Managed & one-click deploy** - Helm chart / Kubernetes manifests and a hosted option.
- **Proactive agents** - long-running background and scheduled agents with richer Deep Agent tooling.
- **Cost & routing** - budget alerts and automatic model routing for cost and latency.
