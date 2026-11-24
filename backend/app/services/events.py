"""Live progress events on Redis Streams.

One stream per project run (``cortex:events:{project}:{run}``). Redis Streams
(rather than pub/sub) give SSE clients replay: a reconnecting browser sends
``Last-Event-ID`` and resumes exactly where it left off, from any API instance.
Streams are length-capped and expire after ``EVENT_STREAM_TTL_HOURS``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.graph.events import EventType, WorkflowEvent

log = get_logger(__name__)

MAX_STREAM_LEN = 5_000
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted", "awaiting_approval"}


def stream_key(project_id: str, run: int) -> str:
    return f"cortex:events:{project_id}:{run}"


def is_terminal(event: dict[str, Any]) -> bool:
    """Events after which the stream for this run has nothing more to say (for now).

    Only the run manager's STATUS events count: they are emitted *after* the final
    state is committed to the database. The graph's own ``completed``/``error``
    events fire earlier, so closing on them would let a client read stale status.
    """
    return event.get("type") == EventType.STATUS.value and (
        event.get("data", {}).get("status") in TERMINAL_STATUSES
    )


class RedisEventSink:
    """EventSink that appends to the run's Redis stream. Never raises into the workflow."""

    def __init__(self, redis: Redis, project_id: str, run: int, ttl_hours: int) -> None:
        self.redis = redis
        self.key = stream_key(project_id, run)
        self.ttl = ttl_hours * 3600

    async def emit(self, event: WorkflowEvent) -> None:
        try:
            await self.redis.xadd(
                self.key,
                {"data": event.model_dump_json()},
                maxlen=MAX_STREAM_LEN,
                approximate=True,
            )
            await self.redis.expire(self.key, self.ttl)
        except RedisError as exc:  # progress streaming is best-effort; the DB is the record
            log.warning("event_publish_failed", error=type(exc).__name__, key=self.key)


async def read_events(
    redis: Redis, key: str, last_id: str = "0", block_ms: int = 15_000
) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
    """Yield (event_id, event) forever; event is None on an idle timeout (heartbeat)."""
    cursor = last_id
    while True:
        result: Any = await redis.xread({key: cursor}, block=block_ms, count=100)
        if not result:
            yield cursor, None
            continue
        for _stream, entries in result:
            for entry_id, fields in entries:
                cursor = entry_id if isinstance(entry_id, str) else entry_id.decode()
                data: Any = fields
                raw = data.get("data") or data.get(b"data")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                try:
                    yield cursor, json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    continue
