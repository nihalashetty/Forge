"""Tracked fire-and-forget background tasks.

`asyncio.create_task` without keeping a reference lets the task be garbage-collected
before it finishes and silently swallows exceptions. `spawn()` keeps a strong
reference until completion, logs any exception, and bounds total in-flight tasks so a
flood of webhook posts can't spawn unbounded coroutines (audit F4).

In-process only. For real durability/backpressure across workers, route through the
arq worker queue (see `forge.worker`); this is the lightweight always-available path.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("forge.tasks")

_tasks: set[asyncio.Task] = set()
# Hard ceiling on concurrent background tasks (backpressure). Excess spawns are rejected
# so an attacker can't exhaust memory/event-loop by flooding the webhook endpoint.
_MAX_BACKGROUND_TASKS = 256


def spawn(coro, *, name: str | None = None) -> bool:
    """Schedule `coro` as a tracked background task. Returns False (and closes the coro)
    if the in-flight ceiling is reached, so callers can shed load / return 503."""
    if len(_tasks) >= _MAX_BACKGROUND_TASKS:
        log.warning("background task ceiling reached (%d); rejecting %s", _MAX_BACKGROUND_TASKS, name)
        coro.close()
        return False
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("background task %s failed", name or t.get_name(), exc_info=exc)

    task.add_done_callback(_done)
    return True


async def drain(timeout: float = 10.0) -> None:
    """Await outstanding background tasks (best-effort) on shutdown."""
    if not _tasks:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*list(_tasks), return_exceptions=True), timeout=timeout)
    except TimeoutError:
        log.warning("timed out draining %d background task(s)", len(_tasks))
