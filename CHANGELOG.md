# Changelog

All notable changes to Forge are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Tool sets, MCP server, governance & portability
- **Tool sets** (new): a describable, many-to-many group of tools that does two jobs at once —
  it organizes the Tools screen (folders/filter chips) *and* is the unit of assignment and
  exposure. Grant an agent a whole set (`agent.config.toolsets`, resolved to member tools at
  compile time) and publish a set as a GitHub-style **MCP toolset**.
- **Per-project MCP server — full transport + auth.** The exposed server now speaks native
  **Streamable-HTTP / SSE** (Claude Desktop, Cursor, VS Code connect **directly — no `mcp-remote`
  bridge**), with the legacy request/response JSON-RPC POST preserved for simple clients; both
  share one auth + tool-resolution core. Three ways to authenticate: a shared **project API key**
  (server-to-server, no identity), a per-user **personal access token** (PAT, `forge_pat_…`), and
  optional **OAuth 2.1** (Dynamic Client Registration + PKCE S256, audience-bound tokens;
  default-off behind `FORGE_MCP_OAUTH_ENABLED`). The exposed surface is exactly the enabled tools
  of **exposed tool sets**; knowledge, Q&A, and a whole workflow can also be published as MCP
  tools. A least-privileged **connector** role can self-serve MCP tokens and call tools but sees
  no projects/settings.
- **Per-user identity over MCP + connected credentials.** A project-scoped session token or PAT
  resolves to an `end_user`, threaded into the run so entitlement gating and `{{ctx.*}}` injection
  act per user. An OAuth auth-provider can key its token bundle **per end user**
  (`per_user_context_keys`); the app owner stores each user's bundle via the new **connections API**
  (`PUT/GET/DELETE /v1/projects/{id}/auth-providers/{apId}/connections/{endUserId}`). No MCP token
  is ever passed downstream — Forge holds a separate per-user credential.
- **Guardrails & Egress** (new): a single project-level I/O policy in **Settings → Guardrails &
  Egress** (admin-gated), enforced **by default on every agent** — no per-agent wiring. Content
  guardrails (PII redaction, custom `Label = regex` patterns, blocked terms with
  redact/mask/hash/block/flag) run locally in-process; the network egress policy (block-private +
  allow/deny domain lists) is applied to every REST/GraphQL tool, webhook, `web_fetch`, and SQL
  host. A project may only **tighten** inherited egress, never loosen it.
- **Import / Export (portability)** (new): export **tools, workflows, components, and agents** to a
  portable `forge.bundle/1` JSON file and import them into another project. Secret **values never
  leave** (only `secret://…` references travel; import warns you to recreate them); imports **never
  overwrite** (new ids, auto-rename on name collision) and remap in-bundle references. Available
  from each list screen's toolbar; import requires the `editor` role.
- **Model catalog from the backend**: the provider/model list is served by the API as one source
  of truth (was duplicated in the web app).

### Changed (console & runtime)
- **Console reskin**: a shadcn-style **neutral + indigo** design system and a minimal sidebar nav;
  Traces now read like a chat history; unified screen headings and bare icons app-wide.
- **Performance:** cut interactive chat latency by eliminating a ~4s cold-connection DNS (AAAA)
  stall on outbound LLM/REST calls and reusing pooled LLM connections
  (`FORGE_PREFER_IPV4_EGRESS`, default on).

### Fixed (this cycle)
- Map `openai_moderation` middleware flags to `langchain-openai >=1.3`.
- Group a HITL pause+resume into a single trace turn, and stop recording a HITL interrupt as a
  span error.
- Classifier sends one bounded human turn for cross-provider compatibility; the agent binds at
  most one tool per function name.

### Feature-bounty fixes (correctness, governance, and DX)
- **Entity version history** (new): every save of a workflow/agent/tool/component/auth-provider/
  knowledge-source/project snapshots to `entity_versions`; view + restore in the console; retention
  pruned to a configurable `version_history_limit`.
- **Engine correctness:** Loop nodes no longer crash past ~8 iterations (run `recursion_limit` is
  set); many previously-ignored node/middleware options now work (Join reducer, parallel
  isolation/ordering/timeout, tenant-budget USD cap + per-run token scope, guardrail
  apply_to/redact/flag, model_retry retry_on, subworkflow input/output mapping, Transform jq, LLM
  `{{state}}` templating, agent-node dynamic prompt/model); validation now errors on undeclared
  state-key writes + branches-without-condition.
- **RAG grounding:** default relevance floor calibrated to the local embedder (0.18 → 0.6),
  thresholds the true cosine in hybrid mode + a rerank floor (so off-topic → "I don't know"),
  chunk citations, per-page crawl provenance (+robots/limits), MMR, resumable batched ingest.
- **Isolation/privacy:** per-user long-term memory scope; response-cache keyed by tenant/user/auth;
  Postgres RLS actually wired (per-transaction tenant GUC); MCP `stdio` gated + external-MCP SSRF
  screening; tool-I/O trace redaction on by default in production.
- **Reliability:** webhook + `/run` idempotency; scheduler on by default with an atomic
  double-fire-safe claim; HITL TOCTOU + chained-interrupt + timeout fixes; outbound channel retry
  with delivery status; run cancellation; transient-only tool retries.
- **Observability:** OTel export fixed (wall-clock times + real span hierarchy); cost accounting
  handles prompt-cache tiers + dated/unlisted models; retriever/embedding spans; evals gain
  concurrency, persisted history + regression gate, more scorers, robust judge.
- **Platform/governance:** per-project RBAC + scoped revocable API keys; auth rate-limiting,
  refresh rotation, logout, password-reset/verify, optional TOTP MFA; project budgets +
  allowed-models enforcement; scheduled retention purge; audit pagination/export + secret.read;
  fail-closed public rate limiter; extended hardening guard; workspace management; worker DLQ.
- **MCP:** exposed-server rate-limited + per-project tool allow-list; expose a whole workflow as
  an MCP tool (`mcp_expose_workflow`).
- **Semantic caching** wired as an agent middleware (was built but unreachable).
- **Console:** Settings redesigned with a section sidebar (incl. a model-pricing editor); a
  restrained de-colored palette; version-history drawer; canvas unsaved-changes guard + undo/redo
  + copy-paste; Playground Stop + real thread reset; Deep Agent config panel.

### Added
- Project developer meta: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, this
  changelog, and GitHub issue/PR templates.
- Lightweight `GET /v1/projects/{id}/counts` endpoint powering the sidebar badge counts.
- In-flight GET de-duplication in the web API client (collapses duplicate concurrent
  requests into one).
- Characterization tests for the stats rollups (`apps/api/tests/test_stats.py`).
- Static type-checking with `mypy` (advisory in CI; gradual adoption) and a `CodeQL`
  workflow + Dependabot for supply-chain updates.

### Changed
- **Performance:** dashboard and project stats now compute rollups as SQL aggregates
  (`COUNT`/`SUM` + `GROUP BY`) instead of loading a tenant's entire trace history into
  memory. Output is unchanged.
- **Performance:** the project sidebar fetches one counts call instead of six full lists;
  the dashboard fetches its stats once (was twice); Overview reuses counts.
- **Performance:** the Traces view loads conversations 20 at a time with infinite scroll
  instead of fetching the entire history at once.
- Typed API responses for the counts and stats endpoints (`response_model`), improving the
  generated OpenAPI schema.
- Pinned `ruff` to a reproducible range so CI lint doesn't drift with new rule sets.

### Fixed
- Documentation drift: backend README layout, root README architecture description,
  `TECH_STACK.md` embedder entry, and roadmap chunking strategies.
- Pre-existing lint findings (import order, statement style, mutable `ContextVar` default).

### Removed
- Dead code: an orphaned frontend screen and a half-wired "code" workflow node (frontend
  palette entry + orphan schema with no backend registration).
- Committed local agent tooling that was not part of the product.

## [0.1.0]

- Initial Forge platform: visual agent/workflow builder on LangChain + LangGraph, tools
  (REST/GraphQL/Code/SQL/MCP/built-in), knowledge & RAG, generative-UI components,
  embeddable widget, channels, triggers, evaluations, observability/traces, and a
  production-shaped Docker stack. See the [README](README.md) and [ROADMAP](docs/ROADMAP.md).

[Unreleased]: https://github.com/nihalashetty/Forge/compare/main...HEAD
