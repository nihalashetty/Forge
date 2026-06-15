"""Tiny in-process metrics counter.

Many resilience paths swallow exceptions (a tool fails to materialize, an embedder is
unavailable, a channel reply doesn't send) so one failure can't break a run. That's
correct, but it must not be SILENT. `incr` bumps a named counter and logs once per
name; the counters are exposed at `/v1/metrics` (admin) so operators can see drift.
For multi-worker prod, scrape per-worker or push to a real metrics backend.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter

log = logging.getLogger("forge.metrics")

_counters: Counter[str] = Counter()
_lock = threading.Lock()
_logged: set[str] = set()


def incr(name: str, amount: int = 1, *, detail: str | None = None) -> None:
    with _lock:
        _counters[name] += amount
        first = name not in _logged
        if first:
            _logged.add(name)
    if first:
        log.warning("metric %s first occurrence%s", name, f": {detail}" if detail else "")


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def reset() -> None:
    with _lock:
        _counters.clear()
        _logged.clear()
