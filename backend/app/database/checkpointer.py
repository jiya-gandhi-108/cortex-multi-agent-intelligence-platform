"""LangGraph Postgres checkpointer with pooler-safe connection settings.

Used by the API process and the tests alike, so what's tested is what ships.

* ``prepare_threshold=None`` disables server-side prepared statements, which
  Supabase's shared pooler (and PgBouncer) can't keep across transactions in
  transaction mode. LangGraph's own ``from_conn_string`` enables them, so it
  is deliberately not used.
* Checkpoint tables live in the public schema; on Supabase that schema is exposed
  through the auto-generated Data API, so row-level security is enabled on them
  (no policies = no API access; the table owner used by Cortex is unaffected).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

CHECKPOINT_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


@asynccontextmanager
async def open_checkpointer(conninfo: str, max_size: int = 5) -> AsyncIterator[AsyncPostgresSaver]:
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        async with pool.connection() as conn:
            for table in CHECKPOINT_TABLES:
                await conn.execute(f"ALTER TABLE IF EXISTS {table} ENABLE ROW LEVEL SECURITY")
        yield saver
    finally:
        await pool.close()
