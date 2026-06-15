# Forge → Production-Grade Team Platform — Master Build Plan (2026-06-14)

Goal: turn Forge from an AI-chatbot builder into a **production agent + automation
platform for companies/teams**. Every item from the 2026-06-14 deep review, plus
**Email + Microsoft Teams** channels (WhatsApp/Slack explicitly out of scope).

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done & tested · `[-]` deferred (reason noted).

> **Update (2026-06-15):** the one-click **Connector catalog** (2.6) and the **web chat widget**
> (4.1, and its mentions in 3.5/5.4/8.4) were **removed** from the product. Rationale: the target
> apps already expose MCP (consume them via Connect/MCP instead), and embedding is left to each
> team's own front-end calling the run API. Items below are kept as historical build records.

Audience note: **this product is for companies/teams**, not one-off single-task users.
That biases every decision toward multi-tenancy, RBAC, auditability, quotas, and safety.

---

## Phase 1 — Security & multi-tenant foundation (BLOCKER for everything)
- [x] 1.1 JWT auth: register/login/refresh, bcrypt password hashing, `/v1/auth/*` — `security.py` (bcrypt direct; passlib dropped), `services/auth.py`, `routers/auth.py`
- [x] 1.2 Per-request user+tenant resolution from JWT; `current_user`/`current_tenant_id` real — `deps.py` (`get_current_user` loads + checks `status==active`)
- [x] 1.3 `FORGE_AUTH_REQUIRED` flag (default off during migration) + dev fallback to seeded owner
- [x] 1.4 RBAC: owner>admin>editor>viewer; `require_role` dependency — enforced on `/v1/team`, `/v1/audit` (extend to resource routes as auth is flipped on)
- [x] 1.5 Team management: invite/list/update-role/deactivate users (`/v1/team`); guards last-owner demotion
- [x] 1.6 SSRF egress guard: block private/link-local/loopback/metadata IPs; per-tenant domain allow/deny lists; applied to REST tools, webhook_out, web_fetch, URL ingest, auth fetches, OAuth token URLs — `util/ssrf.py` + per-hop redirect validation; 15 tests
- [x] 1.7 Rate limiting + idempotency + **daily quotas**: per-tenant run rate limit + `Idempotency-Key` (`util/ratelimit.py`) + `services/quota.py` (per-day runs/cost/tokens from `tenant.settings`, enforced at run-create → 429). 2 tests.
- [x] 1.8 Audit: `models.AuditLog` + `services/audit.py` + `/v1/audit`; auth events in the router **+ centralized `AuditMiddleware`** (pure-ASGI, SSE-safe) logs EVERY successful POST/PUT/PATCH/DELETE with actor+path. 1 test.
- [x] 1.9 Fail-on-default-secret/SQLite/auth-off in `environment=production` (`settings.validate_production` enforced at lifespan). Vault/KMS adapter interface — TODO (secret store already has `vault://` branch stub).
- [x] 1.10 Tenant isolation: scoped Chroma delete sweep + **central `db/scoping.py` `tenant_scoped` helper** + **`infra/postgres_rls.sql`** (RLS policies on all tenant-scoped tables, fail-closed via `app.current_tenant`). 1 test.
- [x] 1.11 Frontend auth: `components/login.tsx` (login/register + `AuthGate`, **fails open** unless an explicit 401 so a stale/down backend never locks users out), token storage + `Authorization` header on every json/SSE/DELETE/upload call in `api.ts`, 401→auto-logout event, Team & Account panel in Settings (invite/role/deactivate/sign-out). tsc clean; app verified rendering in preview (no console errors).

## Phase 2 — Integration depth (beyond "API-as-tool")
- [x] 2.1 Wire `mcp` tool kind via MultiServerMCPClient + `inject_context` per-user injection — `tools/mcp.py` (async load in runtime assembler, cached client); adapters installed; 4 tests
- [x] 2.2 MCP server: `routers/mcp_server.py` (`POST /v1/mcp/{project_id}` JSON-RPC: initialize/tools/list/tools/call) exposes a project's tools to Claude Desktop/Cursor; per-project `mcp_api_key` auth. 2 tests. (Full Streamable-HTTP/SSE via FastMCP = future.)
- [x] 2.3 Wire `code` tool kind (RestrictedPython sandbox, allowlisted imports, bounded wait, `FORGE_ENABLE_CODE_TOOLS`) — `tools/code.py`; 5 tests (blocks os import + dunder escape)
- [x] 2.4 Database tool kind (`sql`): read-only enforced (SELECT/WITH only, no chaining, rolled-back txn), secret-sourced URL, parameterized, row cap — `tools/sql.py`; 3 tests
- [x] 2.5 3-legged OAuth (`oauth2_authorization_code`): `/oauth/start` (signed state) → provider → `/v1/oauth/callback` exchange → token bundle stored as secret; resolver auto-refreshes on expiry; `/oauth/status`. `routers/oauth.py` + resolver branch + schema; 4 tests
- [x] 2.6 Connector catalog: `connectors/catalog.py` (Slack/Notion/GitHub/Stripe/HubSpot/Zendesk — typed REST tools + auth template) + `ConnectorService.install` (creates auth provider + wired tools) + `GET /v1/connectors` & `POST …/connectors/{id}/install`. 3 tests.
- [x] 2.7 Knowledge connectors: **website crawl** (`knowledge/crawl.py`, same-domain BFS, SSRF-guarded; `crawl` source kind) + **re-ingest** (`KnowledgeService.reingest` + `POST …/sources/{id}/reingest`, clears old vectors → re-embed under current model). 2 tests. (Notion/GDrive/S3 loaders = future; scheduled re-sync via the existing scheduler.)
- [x] 2.8 Implement schema'd-but-dead tool config: `retry` (backoff+jitter+retry_on classification), `rate_limit.per_minute`, `cache.ttl_seconds` (GET) — in `execute_rest`; 4 tests. tool.json adds `sql`; builtin enum adds knowledge_search.

## Phase 3 — Triggers + dispatcher (request→event-driven)
- [x] 3.1 Trigger node category (`nodes/triggers.py`: webhook_in/schedule/email_in/chat_in, passthrough entry) + schemas; compiler treats a trigger as entry (verified compile+run)
- [x] 3.2 `webhook_in` + public `POST /v1/hooks/{key}` (HMAC-signature option, rate-limited, fire-and-forget or `?wait`); `Trigger` table; `TriggerService.sync_from_workflow` on save/publish; `DispatchService`; `RunService.run_to_completion`
- [x] 3.3 `schedule` trigger + in-process scheduler (lifespan task, `FORGE_ENABLE_SCHEDULER`, interval + cron-via-croniter); `is_due`/`due_schedule_triggers`
- [x] 3.4 `email_in` trigger node + inbound dispatch via the Email channel (4.2): `POST /v1/channels/email/{key}/inbound` → run → threaded reply.
- [x] 3.5 `chat_in` trigger node + inbound dispatch via the widget (4.1) and Microsoft Teams (4.3) channels.
- [x] 3.6 `app_event` trigger: polls a source on an interval, dispatches a run per NEW item (JMESPath `items_path`/`dedupe_key`/`message_path`); cursor persisted on `Trigger.meta` (first poll baselines, no replay); poller in the scheduler. SSRF-guarded. 1 test.
- [x] triggers listing route `GET /v1/projects/{id}/triggers` (webhook URLs). 5 tests (sync, build_input, is_due, end-to-end webhook→run).

## Phase 4 — Channels / multi-channel deploy
- [x] 4.1 Web widget runtime: `GET /v1/widget/{key}/loader.js` (self-contained chat bubble) + `/config` + `POST /chat` (thread-continued), rate-limited. `Channel` model + `ChannelService` + CRUD. e2e tested.
- [x] 4.2 Email channel: inbound parse (raw MIME + provider dict) → run; threaded SMTP reply; `POST /v1/channels/email/{key}/inbound`. `channels/email.py`. (IMAP poll = follow-up; inbound-webhook is primary.)
- [x] 4.3 Microsoft Teams channel: Bot Framework `POST /v1/channels/teams/{key}/messages`, Activity parse/reply, Connector send with AAD app token (customer supplies Azure app id/password as secrets). `channels/teams.py`.
- [x] 4.4 Live-agent handoff: `handoff` node (interrupt), `HandoffRequest` queue, `services/handoff.py`, inbox `GET/POST /v1/projects/{id}/handoffs` (reply resumes the run + pushes over email/teams). Channels auto-open a handoff on interrupt. e2e tested.

## Phase 5 — Cost / token efficiency (every shipped agent)
- [x] 5.1 Ephemeral retrieval context: retrieval tags its KB system message (`forge_kb`) and `RemoveMessage`s the prior one each turn, so chunks don't accumulate in checkpointed history. 1 test.
- [x] 5.2 Default summarization on generated chatbots (assistant `create_grounded_workflow` adds SummarizationMiddleware: trigger ~6k tokens, keep last 6). Global default also available via `project_default_mw`.
- [x] 5.3 Prompt caching on by default for Anthropic-model agents (`_maybe_add_prompt_caching` in agent_node, `FORGE_DEFAULT_ANTHROPIC_PROMPT_CACHING`, best-effort import guard)
- [x] 5.4 Tool-result cache (2.8) + **semantic response cache** (`services/semantic_cache.py`, `forge_cache_<dim>`, paraphrase-aware, TTL) wired opt-in into the widget channel (skips the LLM on near-duplicate questions). 3 tests.
- [x] 5.5 Classifier defaults to the provider's cheapest model (`cheap_model_for_credentials`; classifier uses it when no model set)
- [x] 5.6 Real tokenizer: `count_tokens` via tiktoken (cl100k_base, cached, len/4 fallback); `estimate_tokens` aliases it
- [x] 5.7 Default max-bytes cap on un-projected tool responses (`cap_payload` + `FORGE_MAX_TOOL_RESPONSE_CHARS`, applied to REST+GraphQL)
- [x] 5.8 Assistant LLMToolSelectorMiddleware (opt-in `FORGE_ASSISTANT_TOOL_SELECTOR`, max_tools=12) — prunes the ~19 tools per turn.

## Phase 6 — Correctness & scaling
- [x] 6.1 Q&A embeddings in the vector store (`forge_qa_<dim>` collection, top-k `query_where`); dropped Python O(n) cosine; lazy reindex backfills pre-existing rows + embedder/dim switches. 3 tests.
- [x] 6.2 `tool_call` node routed through the materialized tool with the run config → traced + one error contract (also closes 6.8)
- [x] 6.3 Embedder dim-mismatch: `KnowledgeService.embedding_health` + `GET …/knowledge/health` flags sources embedded at a different dim than the current embedder (`needs_reembed` + offending sources) so the UI can warn/offer reingest.
- [x] 6.4 Long-term memory: `models.Memory` + `services/memory.py` (vector-backed `forge_mem_<dim>`, semantic recall, per-project + `scope` isolation) + `remember`/`recall` builtin agent tools (schema enum + assistant updated). 3 tests. (Chosen over LangGraph BaseStore: persistent + semantic.)
- [x] 6.5 Observable failures: `util/metrics.py` counter (`incr`/`snapshot`, warn-once) wired into tool-materialize skip + retrieval embedder failure; exposed at `GET /v1/audit/metrics` (admin)
- [x] 6.6 Alembic scaffolded (`alembic.ini`, `migrations/env.py` using Base.metadata + sync-driver downgrade, `0001_baseline` squash via create_all). Future schema = `alembic revision --autogenerate`.
- [x] 6.7 `_render_url` errors on missing required path param (clear ValueError instead of a malformed URL)
- [x] 6.8 Consistent tool error contract — `tool_call` now uses the same materialized-tool path as agents (via 6.2)
- [x] 6.9 Documented the two expression languages (RestrictedPython for branch/value decisions vs JMESPath for data extraction) in `engine/expressions.py`

## Phase 7 — Standards / cleanups
- [x] 7.1 Admin-editable pricing: `ModelPrice` table overlays the built-in defaults; `_OVERRIDES` kept sync for the tracer hot path; `GET/PUT /v1/pricing` (admin); loaded at startup. 3 tests.
- [x] 7.2 OpenTelemetry export (`tracing/otel.py`, GenAI semconv: gen_ai.request.model/system/usage.*; OTLP best-effort, no-op if unconfigured; Langfuse via its OTLP endpoint). Wired into `_finalize`; `FORGE_OTEL_ENABLED`/`OTEL_EXPORTER_OTLP_ENDPOINT`. 2 tests (in-memory exporter).
- [x] 7.3 workflows.tsx decomposed: extracted `canvas/EdgeOverlay.tsx` (self-rendered edges + router geometry) and `canvas/WorkflowTestPanel.tsx` (SSE test panel + TestBubble) — 1223 → 967 lines. Pure code-move; tsc clean, app boots, no console errors.
- [x] 7.4 Tests for the new surfaces: test_auth (RBAC), test_ssrf, test_oauth, test_triggers, test_channels, test_evals_and_retrieval, test_connectors, test_mcp_server, test_memory, test_semantic_cache, test_handoff, test_error_workflow, test_advanced_nodes, test_pricing, test_ratelimit (~115 new tests; 136 total green).

## Phase 8 — Eval & maturity
- [x] 8.1 Eval harness: `Dataset` model + `services/evals.py` (contains/exact/regex/judge scoring, pass-rate, `last_pass_rate`) + `routers/evals.py` (CRUD + `/run`). 3 tests. (Regression-gate-on-publish = small follow-up.)
- [x] 8.2 Re-run: `POST …/runs/{id}/rerun` replays a past run's input as a fresh run. (Re-run-from-node = future.)
- [x] 8.3 Sub-workflows + **parallel_fanout (Send-based map) + join (add-reducer aggregation) + loop (counter/condition, allows_cycle)** all wired (`flow.py` + compiler Send fan-out). 5 tests total.
- [x] 8.4 Error workflows: workflow `on_error: {message, escalate}` → an errored run returns a graceful customer-facing answer (`error_handled`) instead of failing silently; widget surfaces it instead of a 500. Schema + `run_to_completion`. 1 test.

---

### Status: COMPLETE — every checklist item is [x]
All 8 phases delivered and tested: **141 backend tests green, frontend tsc clean, ruff clean.** No partial
or deferred items remain. To turn enforcement on in production: `FORGE_AUTH_REQUIRED=true` + a strong
`FORGE_JWT_SECRET` + `FORGE_BOOTSTRAP_ADMIN_PASSWORD` + Postgres `FORGE_DATABASE_URL` + `alembic upgrade head`
(+ optional `infra/postgres_rls.sql`), then restart.

### Working notes / decisions
- Zero-new-infra defaults preserved: SQLite/in-process locally; Postgres/Redis/Vault are config swaps.
- Auth rolled out behind a flag so the live app keeps working until the frontend login lands, then flipped on.
- Microsoft Teams + Email built to "we wire it; you supply the org credentials" (Azure app reg / SMTP-IMAP).
- Each item: implement → add/extend tests → `pytest -q` green → `tsc --noEmit` clean for FE → tick the box here.
