from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.core.config import LLMProvider
from app.models.enums import ResearchDepth
from app.schemas.common import APIModel


class ProviderStatus(APIModel):
    provider: LLMProvider
    configured: bool  # server has credentials; the key itself is never returned
    models: list[str]


class SettingsRead(APIModel):
    preferred_provider: LLMProvider
    preferred_model: str | None
    default_research_depth: ResearchDepth
    max_research_iterations: int | None
    max_tokens_per_project: int | None
    max_cost_usd_per_project: Decimal | None
    require_plan_approval: bool
    updated_at: datetime
    providers: list[ProviderStatus] = Field(default_factory=list)
    server_limits: dict[str, float] = Field(default_factory=dict)


class SettingsUpdate(APIModel):
    """Partial update: only fields present in the request body change."""

    preferred_provider: LLMProvider | None = None
    preferred_model: str | None = Field(default=None, max_length=128)
    default_research_depth: ResearchDepth | None = None
    max_research_iterations: int | None = Field(default=None, ge=0, le=5)
    max_tokens_per_project: int | None = Field(default=None, ge=5_000, le=2_000_000)
    max_cost_usd_per_project: Decimal | None = Field(default=None, ge=0, le=100)
    require_plan_approval: bool | None = None
