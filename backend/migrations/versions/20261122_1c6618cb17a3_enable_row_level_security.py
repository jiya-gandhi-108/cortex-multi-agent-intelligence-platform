"""enable row level security

Supabase exposes the public schema through its auto-generated Data API. With RLS enabled
and no policies, that API can read and write nothing; Cortex connects as the table owner,
which bypasses RLS, so the application is unaffected. Harmless on plain Postgres.

Revision ID: 1c6618cb17a3
Revises: 6b8839e233e3
Create Date: 2026-11-22 18:07:22.613427+00:00
"""

from collections.abc import Sequence

from alembic import op


revision: str = "1c6618cb17a3"
down_revision: str | None = "6b8839e233e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    "users",
    "user_settings",
    "research_projects",
    "research_tasks",
    "research_sources",
    "agent_executions",
    "research_reports",
    "evaluation_runs",
    "evaluation_results",
    "alembic_version",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
