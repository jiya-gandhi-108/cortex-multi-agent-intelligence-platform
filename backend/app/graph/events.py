"""Workflow events: the live-progress stream (SSE) and execution audit trail.

Agents and nodes emit through an ``EventSink``. Phase 4 wires a sink that
persists to Postgres and publishes to Redis; tests use ``ListSink``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class EventType(StrEnum):
    STATUS = "status"  # project status changed (queued, awaiting_approval, ...)
    STAGE = "stage"  # workflow moved to a new stage
    PLAN = "plan"  # validated plan available
    APPROVAL_REQUIRED = "approval_required"
    TASK_STARTED = "task_started"
    TASK_RETRY = "task_retry"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_SKIPPED = "task_skipped"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    SOURCES = "sources"
    VERIFICATION = "verification"
    REPORT = "report"
    ERROR = "error"
    COMPLETED = "completed"


class WorkflowEvent(BaseModel):
    type: EventType
    project_id: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: str | None = None
    task_key: str | None = None
    agent: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EventSink(Protocol):
    async def emit(self, event: WorkflowEvent) -> None: ...


class NullSink:
    async def emit(self, event: WorkflowEvent) -> None:
        return None


class ListSink:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    async def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)

    def of(self, kind: EventType) -> list[WorkflowEvent]:
        return [e for e in self.events if e.type is kind]
