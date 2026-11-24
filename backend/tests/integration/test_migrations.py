"""Migration history must work on an empty database and must leave LangGraph's tables alone."""

import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

from tests.conftest import TEST_DB_URL

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
APP_TABLES = {
    "users",
    "user_settings",
    "research_projects",
    "research_tasks",
    "research_sources",
    "agent_executions",
    "research_reports",
    "evaluation_runs",
    "evaluation_results",
}


def _alembic(db_url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={db_url}", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def _conninfo(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _tables(conninfo: str) -> dict[str, bool]:
    with psycopg.connect(conninfo) as conn:
        rows = conn.execute(
            "SELECT relname, relrowsecurity FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'"
        ).fetchall()
    return {name: rls for name, rls in rows}


@pytest.fixture
def fresh_db() -> str:
    base = make_url(TEST_DB_URL)
    name = f"cortex_mig_{uuid.uuid4().hex[:8]}"
    admin = _conninfo(TEST_DB_URL)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_full_round_trip_on_empty_database(fresh_db: str) -> None:
    _alembic(fresh_db, "upgrade", "head")
    tables = _tables(_conninfo(fresh_db))
    assert APP_TABLES <= set(tables)
    assert all(tables[t] for t in APP_TABLES), "every app table has row level security"

    _alembic(fresh_db, "downgrade", "base")
    assert not (APP_TABLES & set(_tables(_conninfo(fresh_db))))

    _alembic(fresh_db, "upgrade", "head")


def test_migrations_never_touch_checkpoint_tables(fresh_db: str) -> None:
    conninfo = _conninfo(fresh_db)
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("CREATE TABLE checkpoints (thread_id text PRIMARY KEY)")
        conn.execute("INSERT INTO checkpoints VALUES ('keep-me')")

    _alembic(fresh_db, "upgrade", "head")
    _alembic(fresh_db, "downgrade", "base")

    with psycopg.connect(conninfo) as conn:
        assert conn.execute("SELECT thread_id FROM checkpoints").fetchall() == [("keep-me",)]


def test_models_match_migrations(fresh_db: str) -> None:
    _alembic(fresh_db, "upgrade", "head")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={fresh_db}", "check"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
