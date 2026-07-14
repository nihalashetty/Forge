"""Bounded retry + backoff for outbound channel delivery (audit E).

Outbound email (SMTP) and Teams (Bot Connector REST) deliveries are transient-failure prone
(a relay hiccup, a 429, a 5xx). Previously a single failure was swallowed and the reply was
lost - and worse, a handoff was still marked 'answered'. `retry_send` gives every outbound
delivery a small, jittered exponential backoff and re-raises the last error when exhausted so
the caller can record a real delivery status.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger("forge.channels.retry")

# Settings wanted (config.py is owned by a parallel agent; using module constants for now):
#   FORGE_CHANNEL_SEND_MAX_ATTEMPTS (int, default 3)
#   FORGE_CHANNEL_SEND_BACKOFF_BASE_SECONDS (float, default 0.5)
CHANNEL_SEND_MAX_ATTEMPTS = 3
CHANNEL_SEND_BACKOFF_BASE_SECONDS = 0.5
CHANNEL_SEND_MAX_BACKOFF_SECONDS = 8.0

T = TypeVar("T")


class ChannelDeliveryError(Exception):
    """A retryable outbound-delivery failure. `retry_after` (seconds), when set, overrides the
    computed backoff for the next attempt - used to honor a Teams/HTTP `Retry-After` on a 429."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


async def retry_send(
    fn: Callable[[int], Awaitable[T]],
    *,
    attempts: int = CHANNEL_SEND_MAX_ATTEMPTS,
    base_delay: float = CHANNEL_SEND_BACKOFF_BASE_SECONDS,
    label: str = "channel send",
) -> T:
    """Call async `fn(attempt)` up to `attempts` times with jittered exponential backoff.

    `fn` raises to signal a retryable failure; raise `ChannelDeliveryError(retry_after=…)` to
    request an explicit wait (server-directed, e.g. a 429). Returns `fn`'s result on the first
    success; re-raises the last exception once attempts are exhausted."""
    last_exc: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await fn(attempt)
        except Exception as e:  # noqa: BLE001 - classify + backoff below
            last_exc = e
            if attempt >= attempts:
                break
            retry_after = getattr(e, "retry_after", None)
            if retry_after is not None:
                delay = float(retry_after)
            else:
                delay = min(base_delay * (2 ** (attempt - 1)), CHANNEL_SEND_MAX_BACKOFF_SECONDS)
                delay += random.uniform(0, base_delay)  # noqa: S311 - jitter, not crypto
            log.warning(
                "%s attempt %d/%d failed (%s); retrying in %.1fs",
                label, attempt, attempts, type(e).__name__, delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
