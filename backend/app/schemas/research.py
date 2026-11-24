from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator

from app.models.enums import ProjectStatus, ResearchDepth
from app.schemas.common import APIModel

FOCUS_AREAS = frozenset(
    {
        "market_size",
        "trends",
        "competitors",
        "pricing",
        "customer_segments",
        "technology",
        "regulation",
        "opportunities",
        "risks",
    }
)


class ExecutionLimits(APIModel):
    """Optional per-project overrides; clamped to user settings and server maxima."""

    max_research_iterations: int | None = Field(default=None, ge=0, le=5)
    max_tokens: int | None = Field(default=None, ge=5_000, le=2_000_000)
    max_cost_usd: Decimal | None = Field(default=None, ge=0, le=100)
    require_plan_approval: bool | None = None


class ProjectCreate(APIModel):
    objective: str = Field(min_length=20, max_length=4_000)
    title: str | None = Field(default=None, min_length=3, max_length=200)
    depth: ResearchDepth | None = None
    focus_areas: list[str] = Field(default_factory=list, max_length=len(FOCUS_AREAS))
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @field_validator("objective")
    @classmethod
    def _clean_objective(cls, v: str) -> str:
        # Collapse control characters; the objective is later embedded in prompts.
        cleaned = "".join(ch if ch.isprintable() or ch in "\n\t" else " " for ch in v)
        return cleaned.strip()

    @field_validator("focus_areas")
    @classmethod
    def _known_focus(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - FOCUS_AREAS)
        if unknown:
            raise ValueError(f"unknown focus areas: {', '.join(unknown)}")
        return sorted(set(v))


class ProjectSummary(APIModel):
    id: uuid.UUID
    title: str
    status: ProjectStatus
    depth: ResearchDepth
    current_stage: str | None
    total_tokens: int
    estimated_cost: Decimal
    run_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None = None
    has_report: bool = False


class ProjectRead(ProjectSummary):
    objective: str
    focus_areas: list[str]
    config: dict[str, object]
    plan: dict[str, object] | None
    error_message: str | None
