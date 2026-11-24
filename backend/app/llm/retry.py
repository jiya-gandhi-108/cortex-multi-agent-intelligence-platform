"""Retry with exponential backoff + jitter for transient provider errors."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger
from app.llm.base import LLMError, LLMTransientError

log = get_logger(__name__)


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Full-jitter exponential backoff: uniform(0, min(cap, base * 2**attempt))."""
    return random.uniform(0, min(cap, base * (2**attempt)))  # noqa: S311 (not crypto)


async def with_retries[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    on_retry: Callable[[int, LLMError, float], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    do_sleep = sleep or asyncio.sleep  # resolved per call, so tests can patch it
    attempt = 0
    while True:
        try:
            return await fn()
        except LLMTransientError as exc:
            if attempt >= max_retries:
                raise
            delay = exc.retry_after if exc.retry_after is not None else backoff_delay(attempt)
            log.warning("llm_retry", attempt=attempt + 1, delay=round(delay, 2), error=str(exc))
            if on_retry:
                on_retry(attempt + 1, exc, delay)
            await do_sleep(delay)
            attempt += 1
