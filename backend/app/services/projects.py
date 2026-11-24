from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import ResearchProject
from app.models.enums import ProjectStatus
from app.repositories.projects import ProjectRepository
from app.schemas.common import Page
from app.schemas.research import ProjectCreate, ProjectRead, ProjectSummary
from app.services.settings import SettingsService, resolve_limits


def derive_title(objective: str, max_len: int = 80) -> str:
    first_line = re.split(r"[\n.?!]", objective, maxsplit=1)[0].strip() or objective.strip()
    if len(first_line) <= max_len:
        return first_line
    cut = first_line[:max_len].rsplit(" ", 1)[0]
    return f"{cut}…"


def _duration(p: ResearchProject) -> float | None:
    if p.started_at and p.completed_at:
        return (p.completed_at - p.started_at).total_seconds()
    return None


def to_summary(p: ResearchProject, has_report: bool) -> ProjectSummary:
    s = ProjectSummary.model_validate(p)
    s.duration_seconds = _duration(p)
    s.has_report = has_report
    return s


def to_read(p: ResearchProject, has_report: bool) -> ProjectRead:
    r = ProjectRead.model_validate(p)
    r.duration_seconds = _duration(p)
    r.has_report = has_report
    return r


class ProjectService:
    def __init__(self, session: AsyncSession, cfg: Settings) -> None:
        self.session = session
        self.cfg = cfg
        self.projects = ProjectRepository(session)
        self.settings = SettingsService(session, cfg)

    async def create(self, user_id: uuid.UUID, data: ProjectCreate) -> ProjectRead:
        user_settings = await self.settings.get_model(user_id)
        project = await self.projects.create(
            user_id,
            title=data.title or derive_title(data.objective),
            objective=data.objective,
            depth=data.depth or user_settings.default_research_depth,
            focus_areas=data.focus_areas,
            config=resolve_limits(self.cfg, user_settings, data.limits),
            status=ProjectStatus.DRAFT,
        )
        return to_read(project, has_report=False)

    async def get_owned(self, user_id: uuid.UUID, project_id: uuid.UUID) -> ResearchProject:
        project = await self.projects.get_owned(project_id, user_id)
        if project is None:
            # 404 for both "missing" and "someone else's": don't leak existence.
            raise NotFoundError("Research project not found")
        return project

    async def read(self, user_id: uuid.UUID, project_id: uuid.UUID) -> ProjectRead:
        project = await self.get_owned(user_id, project_id)
        return to_read(project, await self.projects.has_report(project.id))

    async def list(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: ProjectStatus | None = None,
    ) -> Page[ProjectSummary]:
        rows, total = await self.projects.list_owned(
            user_id, limit=limit, offset=offset, status=status
        )
        return Page[ProjectSummary](
            items=[to_summary(p, h) for p, h in rows], total=total, limit=limit, offset=offset
        )
