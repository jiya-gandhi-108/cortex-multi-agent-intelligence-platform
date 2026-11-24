"""Shared fixtures.

Unit tests never touch the network. Integration tests (``-m integration``)
use a real Postgres database (TEST_DATABASE_URL), migrated with Alembic once
per session and truncated after each test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Never send traces from tests, whatever the developer's shell has set.
os.environ["LANGSMITH_TRACING"] = "false"
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Environment, Settings, get_settings
from app.core.rate_limit import InMemoryRateLimiter

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://cortex:cortex@localhost:5432/cortex_test"
)


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": Environment.TEST,
        "database_url": TEST_DB_URL,
        "redis_url": os.environ.get("REDIS_URL", "redis://localhost:6379/15"),
        "jwt_secret_key": "test-secret-key-that-is-long-enough-0123456789",
        "rate_limit_per_minute": 1000,
        "log_level": "WARNING",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def rate_limiter() -> InMemoryRateLimiter:
    return InMemoryRateLimiter()


# ----------------------------------------------------------------- integration


@pytest.fixture(scope="session")
def migrated_db() -> str:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={TEST_DB_URL}", "upgrade", "head"],
        cwd=BACKEND,
        check=True,
        capture_output=True,
    )
    return TEST_DB_URL


@pytest.fixture
async def db_engine(migrated_db: str, settings: Settings) -> AsyncIterator[None]:
    from app.database import session as db

    await db.dispose_engine()
    db.init_engine(settings)
    try:
        yield
    finally:
        async with db.get_sessionmaker()() as s:
            await s.execute(
                text(
                    "TRUNCATE users, user_settings, research_projects, research_tasks, "
                    "research_sources, agent_executions, research_reports, evaluation_runs, "
                    "evaluation_results CASCADE"
                )
            )
            await s.commit()
        await db.dispose_engine()


@pytest.fixture
async def client(
    db_engine: None, settings: Settings, rate_limiter: InMemoryRateLimiter
) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app(settings=settings, rate_limiter=rate_limiter)
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def register(client: AsyncClient, email: str, password: str = "s3cure-pass") -> str:
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return str(r.json()["token"]["access_token"])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
