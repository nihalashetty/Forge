# Forge API

FastAPI backend for **Forge** - the self-hosted agent platform. Built directly on the
MIT-licensed LangChain v1 + LangGraph v1 framework. **Never** depends on `langgraph-api`
(Elastic 2.0) or LangSmith (commercial).

## Local dev (zero external infra)

The default local stack needs **no Docker, Postgres, or Redis**:

| Concern | Local default | Production swap (config-only) |
|---|---|---|
| Relational DB | SQLite (`aiosqlite`) | Postgres 16 + `pgvector` |
| Run durability | `langgraph-checkpoint-sqlite` | `langgraph-checkpoint-postgres` |
| Vectors | Chroma (embedded, persistent) | Chroma server / Qdrant |
| Cache / queue | in-process | Redis 7 + arq |
| Secrets | Fernet (local key file) | Vault / cloud KMS |

```bash
cd apps/api
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on *nix)
pip install -e ".[dev]"           # core + test deps only
pip install -e ".[dev,all]"       # full local stack (vectors + providers + knowledge + MCP)

cp ../../.env.example .env
uvicorn forge.main:app --reload --port 8000
pytest                            # validate the engine
```

## Layout

```
apps/api/
  forge/
    main.py              FastAPI app factory + lifespan (DB init, checkpointer, scheduler/reaper)
    config.py            Settings (pydantic-settings, env-driven) + production hardening guard
    deps.py              FastAPI dependencies: session, auth/tenant resolution, RBAC
    security.py          Auth primitives: bcrypt password hashing + JWT mint/verify
    audit_middleware.py  ASGI middleware that audits successful mutations
    queue.py, worker.py  Optional arq/Redis queue + worker for offloaded runs (prod)
    db/                  async engine, session, tenant scoping, dev seed/bootstrap
    models/              SQLAlchemy ORM (tenants, projects, workflows, runs, traces, ...)
    schemas/             Pydantic request/response DTOs + shared JSON-Schema loader/validator
    services/            business logic (ProjectSvc, WorkflowSvc, RunSvc, assistant, ...)
    routers/             HTTP + SSE endpoints (incl. assistant, mcp_server, oauth, embed)
    engine/              the heart: registry, compiler, state, middleware_compiler, context
    nodes/               node-type factories (start, end, agent, llm, tool_call, flow, rag, triggers)
    tools/               tool materialization (rest, graphql, code, sql, mcp, builtin) + projection
    auth_providers/      Auth Provider resolver (csrf_session, oauth2, bearer, ...)
    secrets/             Fernet-encrypted, reference-only secret store
    channels/            email deployment surface
    knowledge/           EmbeddingStore (Chroma), ingestion/crawl, splitter, hybrid + rerank
    tracing/             ForgeTracer callback + span sink + pricing + tool-I/O capture
    util/                cross-cutting helpers (SSRF guard, http client, rate limit, mailer, ...)
    assistant_skills/    skill(s) the in-product build assistant loads
  migrations/            Alembic migrations (prod schema path; SQLite auto-creates in dev)
  tests/                 pytest suite (engine, tools, knowledge, auth, security, ...)
```
