# Forge

**A self-hosted platform for visually building, testing, and shipping AI agents & workflows** —
on top of the MIT-licensed LangChain v1 + LangGraph v1 framework. Register tools (REST/GraphQL/
code/MCP), wire agents and node graphs on a canvas, control token cost with response projection,
attach a middleware stack for fine-grained behavior, add knowledge (RAG) + Q&A, then ship the
result over email, Microsoft Teams, an API, or an MCP server.

> Never depends on `langgraph-api` (Elastic 2.0) or LangSmith (commercial). All open source.

## Monorepo layout

```
forge/
├── apps/
│   ├── api/        FastAPI backend — the engine (compiler, registry, middleware), tools,
│   │              auth, knowledge, tracing, MCP server, build assistant.   [Python]
│   └── web/        Next.js console — canvas, config panels, playground, traces.  [TS/React]
├── packages/
│   └── schemas/    Shared JSON Schemas — the single source of truth (Doc 4).
│                  Imported by the backend validator/compiler AND the frontend <SchemaForm>.
├── docs/           Design docs (research, technical design, UI spec, schema contracts) + roadmap.
└── infra/          docker-compose, Dockerfiles, migrations (production swaps).
```

The **shared schemas** are the contract that keeps the three consumers in lockstep: the
backend **validator** (reject bad configs on save), the **compiler** (`compile_workflow`,
`build_middleware`), and the frontend **`<SchemaForm>`** (forms generated from the same files).

## Getting started

### Prerequisites

- **Python** 3.11–3.13 (the backend engine)
- **Node** 18+ and **pnpm** (the web console)
- Nothing else — the local stack runs on SQLite + embedded Chroma + an in-process cache,
  so no Docker, Postgres, or Redis is required to start.

### 1. Configure environment

```bash
cp .env.example .env        # copy .env.example to .env on *nix
copy .env.example .env      # ...or this on Windows
```

Open `.env` and fill in the values you need (e.g. an LLM API key). Everything is optional to
boot the app, but agents that call a model need a provider key. **Never commit your `.env`** —
it is already git-ignored.

### 2. Backend (FastAPI engine)

```bash
cd apps/api
python -m venv .venv && .venv\Scripts\activate     # source .venv/bin/activate on *nix
pip install -e ".[dev,all]"                         # engine + tests + vectors/providers/knowledge/MCP
pytest                                              # optional: validate the engine (offline)
uvicorn forge.main:app --reload --port 8000         # http://localhost:8000/docs
```

Production swaps (Postgres+pgvector, Redis, Vault) are config-only; see
[apps/api/README.md](apps/api/README.md).

### 3. Frontend (Next.js console)

In a second terminal, from the repo root:

```bash
pnpm install
pnpm --filter web dev                               # http://localhost:3000
```

Open **http://localhost:3000** for the console and **http://localhost:8000/docs** for the API.

## Status

This is an in-progress, phased build. See **[docs/ROADMAP.md](docs/ROADMAP.md)** for what's
done, what's next, and the deliberate deviations from the original design docs.
