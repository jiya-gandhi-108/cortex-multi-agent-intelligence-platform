"""Verify the local environment: config loads, Postgres and Redis reachable,
and which external integrations have credentials (names only, never values).

Usage (from backend/):  .venv/bin/python ../scripts/check_env.py
Exit code is non-zero if a required service is unreachable.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.database.session import dispose_engine, init_engine


async def main() -> int:
    settings = get_settings()
    ok = True
    print(f"environment     : {settings.environment}")

    try:
        engine = init_engine(settings)
        async with engine.connect() as conn:
            version = (await conn.execute(text("select version()"))).scalar_one()
        print(f"postgres        : OK ({version.split(',')[0]})")
    except Exception as exc:
        ok = False
        print(f"postgres        : FAIL ({type(exc).__name__})")
    finally:
        await dispose_engine()

    try:
        redis = Redis.from_url(settings.redis_url)
        await redis.ping()
        await redis.aclose()
        print("redis           : OK")
    except Exception as exc:
        ok = False
        print(f"redis           : FAIL ({type(exc).__name__})")

    for name, present in settings.integration_status().items():
        print(f"{name:<16}: {'configured' if present else 'not configured (mock-only)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
