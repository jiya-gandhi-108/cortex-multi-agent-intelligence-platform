"""Project data access. Every method requires the caller's ``user_id``.

There is deliberately no ``get(project_id)`` without an owner: the only way
to load a project is through its owner, which makes cross-user access a
query that returns nothing rather than a check someone can forget.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ResearchProject, ResearchReport
from app.models.enums import ProjectStatus


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, user_id: uuid.UUID, **fields: Any) -> ResearchProject:
        project = ResearchProject(user_id=user_id, **fields)
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def get_owned(
        self, project_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> ResearchProject | None:
        stmt = select(ResearchProject).where(
            ResearchProject.id == project_id, ResearchProject.user_id == user_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_owned(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: ProjectStatus | None = None,
    ) -> tuple[list[tuple[ResearchProject, bool]], int]:
        base = select(ResearchProject).where(ResearchProject.user_id == user_id)
        if status is not None:
            base = base.where(ResearchProject.status == status)

        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()

        has_report = (
            exists().where(ResearchReport.project_id == ResearchProject.id).label("has_report")
        )
        rows = await self.session.execute(
            base.add_columns(has_report)
            .order_by(ResearchProject.created_at.desc(), ResearchProject.id)
            .limit(limit)
            .offset(offset)
        )
        return [(p, bool(h)) for p, h in rows.all()], int(total)

    async def has_report(self, project_id: uuid.UUID) -> bool:
        stmt = select(exists().where(ResearchReport.project_id == project_id))
        return bool((await self.session.execute(stmt)).scalar())
