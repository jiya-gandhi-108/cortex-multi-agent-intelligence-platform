from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import LLMProvider, Settings
from app.models import UserSettings
from app.repositories.users import SettingsRepository
from app.schemas.research import ExecutionLimits
from app.schemas.settings import ProviderStatus, SettingsRead, SettingsUpdate


def available_providers(cfg: Settings) -> list[ProviderStatus]:
    return [
        ProviderStatus(
            provider=LLMProvider.GROQ,
            configured=cfg.provider_configured(LLMProvider.GROQ),
            models=[cfg.groq_model_reasoning, cfg.groq_model_fast],
        ),
        ProviderStatus(
            provider=LLMProvider.GEMINI,
            configured=cfg.provider_configured(LLMProvider.GEMINI),
            models=[cfg.gemini_model],
        ),
    ]


def server_limits(cfg: Settings) -> dict[str, float]:
    return {
        "max_research_iterations": cfg.max_research_iterations,
        "max_tokens_per_project": cfg.max_tokens_per_project,
        "max_cost_usd_per_project": cfg.max_cost_usd_per_project,
    }


def resolve_limits(
    cfg: Settings, user: UserSettings, overrides: ExecutionLimits | None = None
) -> dict[str, Any]:
    """Effective limits = min(server max, user default, per-project override).

    A user can only make limits *stricter* than the server allows.
    """
    o = overrides or ExecutionLimits()

    def pick(server: Any, *candidates: Any) -> Any:
        values = [c for c in candidates if c is not None]
        return min([server, *values]) if values else server

    max_cost = pick(
        Decimal(str(cfg.max_cost_usd_per_project)), user.max_cost_usd_per_project, o.max_cost_usd
    )
    return {
        "max_research_iterations": pick(
            cfg.max_research_iterations, user.max_research_iterations, o.max_research_iterations
        ),
        "max_tokens": pick(cfg.max_tokens_per_project, user.max_tokens_per_project, o.max_tokens),
        "max_cost_usd": float(max_cost),
        "max_agent_retries": cfg.max_agent_retries,
        "require_plan_approval": (
            o.require_plan_approval
            if o.require_plan_approval is not None
            else user.require_plan_approval
        ),
        "provider": user.preferred_provider,
        "model": user.preferred_model,
    }


class SettingsService:
    def __init__(self, session: AsyncSession, cfg: Settings) -> None:
        self.repo = SettingsRepository(session)
        self.cfg = cfg

    def _read(self, s: UserSettings) -> SettingsRead:
        out = SettingsRead.model_validate(s)
        out.providers = available_providers(self.cfg)
        out.server_limits = server_limits(self.cfg)
        return out

    async def get(self, user_id: uuid.UUID) -> SettingsRead:
        return self._read(await self.repo.get_or_create(user_id))

    async def get_model(self, user_id: uuid.UUID) -> UserSettings:
        return await self.repo.get_or_create(user_id)

    async def update(self, user_id: uuid.UUID, patch: SettingsUpdate) -> SettingsRead:
        s = await self.repo.get_or_create(user_id)
        changes = patch.model_dump(exclude_unset=True)
        if "preferred_provider" in changes and changes["preferred_provider"] is not None:
            changes["preferred_provider"] = changes["preferred_provider"].value
            # Model names are provider-specific; switching provider resets the model.
            if "preferred_model" not in changes:
                changes["preferred_model"] = None
        for field in ("preferred_provider", "default_research_depth", "require_plan_approval"):
            if field in changes and changes[field] is None:
                del changes[field]  # non-nullable columns: null means "leave as is"
        for key, value in changes.items():
            setattr(s, key, value)
        await self.repo.session.flush()
        await self.repo.session.refresh(s)
        return self._read(s)
