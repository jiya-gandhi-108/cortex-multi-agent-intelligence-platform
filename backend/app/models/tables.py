"""ORM tables.

Ownership: only ``users`` and ``research_projects`` carry ``user_id``. Every
other research table hangs off a project, so isolation is enforced by always
resolving a project through ``(project_id, user_id)`` first (see repositories).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import (
    ExecutionStatus,
    ProjectStatus,
    ReportFormat,
    ResearchDepth,
    TaskPriority,
    TaskStatus,
)


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint storing enum *values* (not names)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class UpdatedAtMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=utcnow,
        onupdate=utcnow,
    )


# --------------------------------------------------------------------------- users


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    # Stored lower-cased by the repository; unique index enforces one account per address.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    settings: Mapped[UserSettings | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserSettings(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    preferred_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="groq")
    preferred_model: Mapped[str | None] = mapped_column(String(128))
    default_research_depth: Mapped[ResearchDepth] = mapped_column(
        str_enum(ResearchDepth, "research_depth"), nullable=False, default=ResearchDepth.STANDARD
    )
    # Per-user guard rails; always clamped to the server maxima at run time.
    max_research_iterations: Mapped[int | None] = mapped_column(Integer)
    max_tokens_per_project: Mapped[int | None] = mapped_column(Integer)
    max_cost_usd_per_project: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    require_plan_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship(back_populates="settings")


# ------------------------------------------------------------------------ projects


class ResearchProject(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "research_projects"
    __table_args__ = (
        Index("ix_research_projects_user_created", "user_id", "created_at"),
        Index("ix_research_projects_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        str_enum(ProjectStatus, "project_status"), nullable=False, default=ProjectStatus.DRAFT
    )
    depth: Mapped[ResearchDepth] = mapped_column(
        str_enum(ResearchDepth, "project_depth"), nullable=False, default=ResearchDepth.STANDARD
    )
    focus_areas: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Resolved execution limits for this run (iterations, tokens, cost, approval).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    current_stage: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Verification/quality/usage summary of the latest run (for status + dashboards).
    run_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tasks: Mapped[list[ResearchTask]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    reports: Mapped[list[ResearchReport]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class ResearchTask(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "task_key", name="uq_research_tasks_project_task_key"),
        Index("ix_research_tasks_project_status", "project_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False
    )
    task_key: Mapped[str] = mapped_column(String(32), nullable=False)  # planner id, e.g. task_001
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    task_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.PENDING
    )
    priority: Mapped[TaskPriority] = mapped_column(
        str_enum(TaskPriority, "task_priority"), nullable=False, default=TaskPriority.MEDIUM
    )
    dependencies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ResearchProject] = relationship(back_populates="tasks")


class ResearchSource(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "normalized_url", name="uq_research_sources_project_url"),
        UniqueConstraint("project_id", "citation_key", name="uq_research_sources_project_citation"),
        Index("ix_research_sources_project_retrieved", "project_id", "retrieved_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_tasks.id", ondelete="SET NULL"), index=True
    )
    # Short stable id the LLM cites (e.g. "S12"); citations are validated against it.
    citation_key: Mapped[str] = mapped_column(String(16), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    extracted_content: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class AgentExecution(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_executions"
    __table_args__ = (Index("ix_agent_executions_project_started", "project_id", "started_at"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_tasks.id", ondelete="SET NULL"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[ExecutionStatus] = mapped_column(
        str_enum(ExecutionStatus, "execution_status"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(128))
    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    execution_time: Mapped[float | None] = mapped_column(Float)  # milliseconds
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchReport(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "research_reports"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_research_reports_project_version"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    report_content: Mapped[str] = mapped_column(Text, nullable=False)
    report_format: Mapped[ReportFormat] = mapped_column(
        str_enum(ReportFormat, "report_format"), nullable=False, default=ReportFormat.MARKDOWN
    )
    # Structured sections + validated citation keys, and the verification summary
    # the report was built from, so the viewer never has to re-parse Markdown.
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[ResearchProject] = relationship(back_populates="reports")


# ---------------------------------------------------------------------- evaluation


class EvaluationRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One execution of the benchmark suite (offline harness check or live)."""

    __tablename__ = "evaluation_runs"

    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # live | offline
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    models: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    depth: Mapped[str] = mapped_column(String(16), nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class EvaluationResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "benchmark_id", name="uq_evaluation_results_run_benchmark"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    benchmark_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # workflow outcome or "error"
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    human_review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    report_markdown: Mapped[str | None] = mapped_column(Text)

    run: Mapped[EvaluationRun] = relationship(back_populates="results")
