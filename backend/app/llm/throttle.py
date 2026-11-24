"""Client-side rate governor for per-minute request and token limits.

Groq enforces limits per organisation per model. Rather than firing requests
and absorbing 429s, each call reserves its estimated tokens in a 60-second
sliding window and waits until the reservation fits. After the call, the
reservation is corrected to actual usage.

Scope: per process. Cortex runs a single API instance by default; with
several instances, provider 429s (handled with retry-after) remain the
backstop. Documented in docs/architecture.md.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Reservation:
    at: float
    tokens: int


@dataclass
class RateGovernor:
    tokens_per_minute: int
    requests_per_minute: int
    window_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _entries: deque[Reservation] = field(default_factory=deque)
    _blocked_until: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def wait_time(self, tokens: int, now: float) -> float:
        """Seconds until a reservation of ``tokens`` fits (0 if it fits now)."""
        while self._entries and now - self._entries[0].at >= self.window_seconds:
            self._entries.popleft()
        block = max(0.0, self._blocked_until - now)

        remaining_tokens = sum(e.tokens for e in self._entries)
        remaining_count = len(self._entries)
        # Drop the oldest k entries until both limits are satisfied. An empty
        # window always admits (a single oversized request goes through alone).
        for k in range(len(self._entries) + 1):
            if remaining_count == 0 or (
                remaining_count < self.requests_per_minute
                and remaining_tokens + tokens <= self.tokens_per_minute
            ):
                if k == 0:
                    return block
                expires = self._entries[k - 1].at + self.window_seconds - now
                return max(block, expires, 0.01)
            remaining_tokens -= self._entries[k].tokens
            remaining_count -= 1
        return block  # unreachable: k == len(entries) leaves an empty window

    async def acquire(self, tokens: int) -> Reservation:
        async with self._lock:  # FIFO: waiters are admitted in arrival order
            while True:
                now = self.clock()
                wait = self.wait_time(tokens, now)
                if wait <= 0:
                    reservation = Reservation(now, tokens)
                    self._entries.append(reservation)
                    return reservation
                await asyncio.sleep(wait)

    def settle(self, reservation: Reservation, actual_tokens: int) -> None:
        reservation.tokens = max(0, actual_tokens)

    def penalise(self, seconds: float) -> None:
        """Provider returned 429 with retry-after: hold all calls that long."""
        self._blocked_until = max(self._blocked_until, self.clock() + max(0.0, seconds))
