# Forge Hardening Pass — 2026-06-18

Implements the complete-workspace analysis: security, functional-correctness, and
production-readiness fixes across the backend, plus deployment artifacts. All 176 backend
tests pass and `ruff check forge migrations` is clean.

## Security (S1–S11)

| ID | Fix | Key files |
|----|-----|-----------|
| S1 | Embed run **stream/resume scoped by `project_id`**, not just tenant — closes cross-project run access via a publishable key. | `services/runs.py`, `routers/embed_public.py`, `routers/runs.py` |
| S2 | Public embed now enforces the **daily quota** + **per-IP** and configurable per-key rate limits; the stream endpoint is rate-limited too. | `routers/embed_public.py`, `config.py` |
| S3 | A caller can no longer attach to a **thread bound to a different identity** (anonymous → verified takeover). | `services/runs.py` (`create_run`) |
| S4 | A non-editor console user can't **self-assert `roles`/`entitlements`** via the run body (stripped). | `routers/runs.py` |
| S5 | **Code tool OFF by default**; prod guard rejects it unless `allow_unsandboxed_code_tools`. | `config.py`, `tools/code.py` |
| S6 | `validate_production` is **fail-closed**: it runs for every non-dev environment, and dangerous defaults are logged loudly at startup. | `config.py`, `main.py` |
| S7 | SQL tool **DSN host runs through the egress (SSRF) policy**; read-only also enforced at the DB layer for Postgres. | `tools/sql.py`, `util/ssrf.py` |
| S8 | OAuth/token/CSRF fetches go through **`guarded_request`** (host validated pre-connect, redirects guarded). | `routers/oauth.py`, `auth_providers/resolver.py` |
| S9 | OAuth callback **HTML-escapes** all reflected values (reflected-XSS). | `routers/oauth.py` |
| S10 | Public/embed error frames return a **generic message**; detail is logged server-side keyed by run id; run-step cost/debug never shown to anonymous end users. | `services/runs.py` |
| S11 | JWT **`kid` + key rotation** (`jwt_secret_previous`), **`jti` + revocation denylist**, shorter access TTL (8h). | `security.py`, `config.py` |

## Functional correctness (F1–F12 + lows)

- **F1** SSE disconnect no longer strands a run at `running` (BaseException-safe `finally` marks it canceled in a shielded session).
- **F2** Quota is enforced **atomically** with run creation (per-tenant admission lock; errored runs don't consume quota).
- **F3** Stale `queued`/`running` runs are **reaped** by a periodic sweep.
- **F4** Webhook fire-and-forget runs use a **tracked, bounded** task helper (failures logged, ceiling enforced).
- **F5** Failed runs now **persist their Trace/Span rows** (observable).
- **F6** Email/Teams conversations **keep context** by mapping the provider conversation id (email `References` root / Teams `conversation.id`) to a persisted thread.
- **F7** Runs sharing a LangGraph thread are **serialized** (per-thread lock) so checkpoint writes can't interleave.
- **F8** Any **HITL interrupt over a text channel** opens a tracked handoff + clear acknowledgement instead of a stale/empty reply.
- **F9** `app_event` poller **claims each tick** before work and updates the seen-set atomically (no lost-update double-dispatch).
- **F10** Router/fanout dead-ends are **logged** (no silent run termination); the validator already warns at publish.
- **F11** Eval runs are **bounded** (≤1000 items), **per-item isolated**, and **quota-gated**.
- **F12** MCP client cache has a **TTL + invalidation** on config change and is **closed on shutdown**.
- **lows** email MIME `decode=True` None guard; semantic-cache id includes tenant+project; `resume` only resumes an interrupted run.

## Production readiness (P1–P5 + P-imp)

- **P1** Optional **arq/Redis worker** offloads non-interactive (webhook/schedule) runs; per-tenant **concurrency caps** bound inline execution. (`worker.py`, `queue.py`)
- **P2** **Postgres checkpointer** backend (`FORGE_CHECKPOINT_BACKEND=postgres`); prod guard rejects sqlite/memory checkpointers.
- **P3** In-process scheduler **defaults off**; single-leader election (`scheduler_leader`); a reaper loop runs on the leader.
- **P4** **Dockerfiles** (api + web), **docker-compose** (Postgres + Redis + api + worker + web), **GitHub Actions CI** (ruff + pytest + web build), `.env.example` files, `.dockerignore`s.
- **P5** **Alembic migration** `0002` (idempotent) for `projects.embed_key` + the `components` table & uniqueness constraint.
- **P-imp** Redis-backed rate-limit/idempotency; `/readyz` + `/livez` + Prometheus `/metrics`; `TrustedHostMiddleware` + trusted-proxy XFF; `BatchSpanProcessor` for OTLP; tests for the embed/identity/scoping surfaces.

## New configuration (env vars)

`FORGE_CHECKPOINT_BACKEND` (sqlite|memory|postgres), `FORGE_CHECKPOINT_POSTGRES_URL`,
`FORGE_REDIS_URL` (activates Redis limiters + worker), `FORGE_ENABLE_SCHEDULER` /
`FORGE_SCHEDULER_LEADER`, `FORGE_MAX_CONCURRENT_RUNS_PER_TENANT`,
`FORGE_EMBED_RATE_LIMIT_PER_MINUTE` / `_PER_IP_PER_MINUTE` / `FORGE_EMBED_STREAM_LIMIT_PER_IP_PER_MINUTE`,
`FORGE_TRUSTED_PROXIES`, `FORGE_TRUSTED_HOSTS`, `FORGE_JWT_SECRET_PREVIOUS`, `FORGE_JWT_KEY_ID`,
`FORGE_ALLOW_UNSANDBOXED_CODE_TOOLS`. See `apps/api/.env.example`.

## Deliberately deferred (need their own design, not a rushed bolt-on)

- **Per-version component fetch (`id@version`)** — requires a component **version-history** table so a frame can render against the exact template it was emitted with. The current single `version` int can't serve old definitions. (Renderer already degrades gracefully on a miss per the prior audit.)
- **Cross-thread memory on LangGraph `BaseStore`** — the Chroma-backed `MemoryService` works and is now tenant-scoped; migrating to a native `PostgresStore`/`RedisStore` is a larger refactor best done deliberately.
- **Per-tenant component theming UI** — the renderer already consumes CSS variables; exposing a tenant theme is primarily frontend work.
- **Full SSE-via-worker relay** — the interactive stream stays inline (it must stream tokens live); a pub/sub relay so any worker can serve any stream is the next scaling step after the worker tier proves out.
