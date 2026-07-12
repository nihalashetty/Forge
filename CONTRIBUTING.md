# Contributing to Forge

Thanks for your interest in improving Forge! This guide covers how to get set up, the
checks your change needs to pass, and our conventions.

## Ways to contribute

- **Report bugs** and **request features** via [issues](https://github.com/nihalashetty/Forge/issues) (templates provided).
- **Improve docs** — the [User Manual](docs/MANUAL.md), READMEs, and `TECH_STACK.md`.
- **Fix or build** — pick up an open issue or propose a change in a discussion first for anything large.

## Development setup

See the [README quick start](README.md#quick-start-local-zero-infra) for the full local
(zero-infra) setup. In short:

```bash
# Backend (FastAPI engine)
cd apps/api
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev,all]"

# Frontend (Next.js console) — from the repo root
pnpm install
pnpm --filter web dev
```

The local stack runs on SQLite + embedded Chroma + an in-process scheduler — no Docker,
Postgres, or Redis required. See [`apps/api/README.md`](apps/api/README.md) for the backend
layout and production swaps.

## Before you open a pull request

Run the same checks CI runs:

**Backend** (from `apps/api`):
```bash
ruff check forge migrations      # lint (pinned; must be clean)
mypy forge                       # type check (advisory today — keep new/changed code clean)
pytest -q                        # tests must pass
```

**Frontend** (from the repo root):
```bash
pnpm --filter web build          # typecheck + build
```

Additional expectations:

- **Keep the shared schemas authoritative.** `packages/schemas` is the single source of
  truth for node/tool config; the backend validator, the compiler, and the frontend
  `<SchemaForm>` all read from it. Update the schema, not one consumer.
- **Add tests** for new behavior. For refactors that must preserve behavior, add a
  characterization test first (see `apps/api/tests/test_stats.py` for the pattern).
- **Type-checking is gradual.** `mypy` is advisory in CI while we clear a backlog on the
  older engine modules, but any file you add or substantially change should be mypy-clean.

## Commit & PR conventions

- **Conventional Commits** for messages: `feat:`, `fix:`, `perf:`, `chore:`, `docs:`,
  `refactor:`, `test:` — optionally scoped, e.g. `perf(stats): …`.
- **Atomic commits** — one logical change per commit; keep history bisectable.
- Open a PR describing the change and the reasoning. Link the issue it addresses.
- Update [`CHANGELOG.md`](CHANGELOG.md) under **Unreleased** for anything user-facing.

## Versioning

Forge follows [Semantic Versioning](https://semver.org/). User-facing changes are recorded
in the changelog and rolled into the next release.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE), the same license as the project.
