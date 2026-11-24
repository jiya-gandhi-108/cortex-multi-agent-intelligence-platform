import os

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings
from app.database.session import build_engine

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    url = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://cortex:cortex@localhost:5432/cortex_test"
    )
    return Settings(_env_file=None, database_url=url)  # type: ignore[call-arg]


async def test_postgres_reachable_and_supports_jsonb() -> None:
    engine = build_engine(_settings())
    try:
        async with engine.connect() as conn:
            value = await conn.execute(text("""select '{"a": 1}'::jsonb ->> 'a'"""))
            assert value.scalar_one() == "1"
    finally:
        await engine.dispose()


async def test_transaction_rolls_back_on_error() -> None:
    engine = build_engine(_settings())
    try:
        async with engine.connect() as conn:
            await conn.execute(text("create temp table t (x int)"))
            with pytest.raises(RuntimeError):
                async with conn.begin_nested():
                    await conn.execute(text("insert into t values (1)"))
                    raise RuntimeError("boom")
            count = (await conn.execute(text("select count(*) from t"))).scalar_one()
            assert count == 0
    finally:
        await engine.dispose()


async def test_redis_reachable() -> None:
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/15"))
    try:
        assert await redis.ping()
    finally:
        await redis.aclose()


async def test_redis_rate_limiter_counts_and_expires() -> None:
    import uuid as _uuid

    from app.core.rate_limit import RedisRateLimiter

    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/15"))
    try:
        limiter = RedisRateLimiter(redis)
        key = f"test:{_uuid.uuid4().hex}"
        results = [await limiter.hit(key, limit=2, window_seconds=60) for _ in range(3)]
        assert [r.allowed for r in results] == [True, True, False]
        ttl = await redis.ttl(next(iter(await redis.keys(f"ratelimit:{key}:*"))))
        assert 0 < ttl <= 61
    finally:
        await redis.aclose()


async def test_redis_rate_limiter_fails_open_when_redis_down() -> None:
    from app.core.rate_limit import RedisRateLimiter

    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    try:
        result = await RedisRateLimiter(dead).hit("k", limit=1, window_seconds=60)
        assert result.allowed
    finally:
        await dead.aclose()


async def test_event_stream_follow_reads_live_entries() -> None:
    import asyncio
    import uuid as _uuid

    from app.graph.events import EventType, WorkflowEvent
    from app.services.events import RedisEventSink, read_events, stream_key

    redis = Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/15"), decode_responses=True
    )
    pid = _uuid.uuid4().hex
    sink = RedisEventSink(redis, pid, 1, ttl_hours=1)
    try:
        await sink.emit(WorkflowEvent(type=EventType.STAGE, project_id=pid, stage="planning"))
        seen: list[str] = []

        async def follow() -> None:
            async for _id, event in read_events(redis, stream_key(pid, 1), block_ms=200):
                if event is None:
                    continue
                seen.append(event["type"])
                if event["type"] == "completed":
                    return

        task = asyncio.create_task(follow())
        await asyncio.sleep(0.1)
        await sink.emit(
            WorkflowEvent(type=EventType.COMPLETED, project_id=pid)
        )  # arrives while blocked
        await asyncio.wait_for(task, 5)
        assert seen == ["stage", "completed"]
        assert 0 < await redis.ttl(stream_key(pid, 1)) <= 3600
    finally:
        await redis.aclose()
