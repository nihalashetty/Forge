# Changelog

All notable changes to Forge are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
