# Forge API

FastAPI backend for **Forge** — the self-hosted agent platform. Built directly on the
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
forge/
  main.py            FastAPI app factory + lifespan
  config.py          Settings (pydantic-settings, env-driven)
  db/                async engine, session, RLS-style tenant scoping
  models/            SQLAlchemy ORM (tenants, projects, workflows, runs, traces, ...)
  schemas/           Pydantic request/response DTOs + JSON-Schema loader/validator
  services/          business logic (ProjectSvc, WorkflowSvc, RunSvc, ...)
  routers/           HTTP + SSE endpoints
  engine/            the heart: registry, compiler, state, middleware_compiler, models
  nodes/             node-type factories (start, end, agent, router, llm, tool_call, ...)
  tools/             tool materialization (rest, graphql, code, mcp, builtin) + projection
  auth_providers/    Auth Provider resolver (csrf_session, oauth2, bearer, ...)
  secrets/           Fernet store (+ vault adapter)
  sandbox/           Sandbox interface (subprocess MVP -> docker -> remote)
  knowledge/         EmbeddingStore (Chroma), ingestion, Q&A, hybrid search
  tracing/           ForgeTracer callback + span sink + pricing
  mcp_server/        multi-tenant FastMCP exposure
  assistant/         the in-product build assistant (meta-agent)
```
