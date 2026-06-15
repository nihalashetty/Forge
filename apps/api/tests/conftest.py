"""Shared test fixtures.

CRITICAL: tests must NOT touch the dev database. We point every persistence path at
a throwaway temp dir *before* any `forge` module imports (the engine, settings, secret
store, and Chroma path are all bound at import time from these env vars).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="forge_tests_"))
os.environ.setdefault("FORGE_DATABASE_URL", f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}")
os.environ.setdefault("FORGE_CHROMA_PATH", (_TMP / "chroma").as_posix())
os.environ.setdefault("FORGE_CHECKPOINT_DB", "memory")
os.environ.setdefault("FORGE_SECRET_KEY_FILE", (_TMP / "master.key").as_posix())
os.environ.setdefault("FORGE_SEED_DEMO", "false")
# Tests hit MockTransport / fake hosts; don't do real DNS in the SSRF guard. The
# guard's blocking logic is covered explicitly in test_ssrf.py (explicit policies).
os.environ.setdefault("FORGE_EGRESS_BLOCK_PRIVATE", "false")
# Auth defaults to ON in the real app; the test suite mostly calls services directly
# without tokens, so run permissive. test_auth forces auth_required=True where it matters.
os.environ.setdefault("FORGE_AUTH_REQUIRED", "false")

import pytest  # noqa: E402

from forge.db.base import init_db  # noqa: E402


@pytest.fixture(autouse=True)
async def _ensure_tables():
    # create_all is idempotent; cheap to run per-test for an isolated DB state.
    await init_db()
    yield
