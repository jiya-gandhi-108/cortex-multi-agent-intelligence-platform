from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.models.enums import ExecutionStatus, ProjectStatus, TaskPriority, TaskStatus
from app.schemas.common import APIModel


class ApprovalRequest(APIModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=500)


class TaskRead(APIModel):
    id: uuid.UUID
    task_key: str
    agent_name: str
    task_description: str
    status: TaskStatus
    priority: TaskPriority
    dependencies: list[str]
    attempts: int
    iteration: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class TaskCounts(APIModel):
    total: int = 0
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0


class StatusRead(APIModel):
    project_id: uuid.UUID
    status: ProjectStatus
    stage: str | None
    run: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    total_tokens: int
    estimated_cost: Decimal
    sources: int
    tasks: TaskCounts
    task_list: list[TaskRead]
    plan: dict[str, Any] | None
    awaiting_approval: bool
    summary: dict[str, Any] | None
    events_url: str


class ExecutionRead(APIModel):
    id: uuid.UUID
    agent_name: str
    task_id: uuid.UUID | None
    attempt: int
    status: ExecutionStatus
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    token_usage: int
    estimated_cost: Decimal
    execution_time: float | None
    error_message: str | None
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None


class AgentSummary(APIModel):
    agent_name: str
    executions: int
    succeeded: int
    failed: int
    tokens: int
    cost: Decimal
    avg_ms: float | None
    last_finished_at: datetime | None


class SourceRead(APIModel):
    id: uuid.UUID
    citation_key: str
    url: str
    title: str | None
    domain: str
    publication_date: datetime | None
    retrieved_at: datetime
    task_key: str | None = None
    fetched: bool = False
    excerpt: str | None = None


class ReportRead(APIModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    report_format: str
    report_content: str
    structured: dict[str, Any]
    verification: dict[str, Any] | None
    created_at: datetime


class ReportSummary(APIModel):
    project_id: uuid.UUID
    project_title: str
    version: int
    created_at: datetime
    evidence_score: float | None = None


class DashboardRead(APIModel):
    total_projects: int
    projects_by_status: dict[str, int]
    completed_tasks: int
    active_executions: int
    total_reports: int
    total_tokens: int
    estimated_cost: Decimal
    recent_projects: list[dict[str, Any]]
    recent_reports: list[ReportSummary]
