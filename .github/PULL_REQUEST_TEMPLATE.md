<!-- Thanks for contributing to Forge! Please fill this out to speed up review. -->

## What & why

<!-- What does this change do, and what problem does it solve? Link the issue: Closes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Performance
- [ ] Refactor (no behavior change)
- [ ] Docs / chore

## Checklist

- [ ] Backend: `ruff check forge migrations` is clean and `pytest -q` passes (from `apps/api`)
- [ ] New/changed backend code is `mypy`-clean
- [ ] Frontend: `pnpm --filter web build` passes (from repo root)
- [ ] Shared schemas (`packages/schemas`) updated if node/tool config changed
- [ ] Tests added/updated for the change (characterization test for behavior-preserving refactors)
- [ ] `CHANGELOG.md` updated under **Unreleased** (for user-facing changes)
- [ ] No secrets, tokens, or customer data in the diff

## Notes for reviewers

<!-- Anything that needs special attention, screenshots for UI changes, migration notes, etc. -->
