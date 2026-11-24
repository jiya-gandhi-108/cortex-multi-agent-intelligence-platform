"""Domain enums shared by ORM models and API schemas."""

from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    DRAFT = "draft"  # created, not started
    QUEUED = "queued"  # start accepted, workflow not yet running
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"  # human checkpoint after planning
    RUNNING = "running"  # specialist agents executing
    VERIFYING = "verifying"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # process died mid-run; resumable from checkpoint

    @property
    def is_terminal(self) -> bool:
        return self in {ProjectStatus.COMPLETED, ProjectStatus.FAILED, ProjectStatus.CANCELLED}

    @property
    def is_active(self) -> bool:
        return self in {
            ProjectStatus.QUEUED,
            ProjectStatus.PLANNING,
            ProjectStatus.RUNNING,
            ProjectStatus.VERIFYING,
            ProjectStatus.REPORTING,
        }


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # dependency failed
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ResearchDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class AgentName(StrEnum):
    """The closed set of agents. The planner may only assign tasks to the three
    specialist agents; the rest are fixed workflow stages."""

    PLANNER = "planner_agent"
    RESEARCH = "research_agent"
    COMPETITOR = "competitor_agent"
    MARKET = "market_analysis_agent"
    VERIFICATION = "verification_agent"
    REPORT = "report_agent"

    @classmethod
    def assignable(cls) -> frozenset[AgentName]:
        return frozenset({cls.RESEARCH, cls.COMPETITOR, cls.MARKET})


class ReportFormat(StrEnum):
    MARKDOWN = "markdown"
