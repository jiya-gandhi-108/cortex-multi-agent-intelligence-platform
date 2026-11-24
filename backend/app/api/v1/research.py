"""Research project endpoints.

Phase 2: create / list / get. Start, cancel, status, agents, executions,
sources, report and the SSE stream are added in Phase 4 once the workflow
exists, so none of them are exposed as non-functional stubs.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep, user_rate_limit
from app.models.enums import ProjectStatus
from app.schemas.common import ERROR_RESPONSES, Page, PageParams
from app.schemas.research import ProjectCreate, ProjectRead, ProjectSummary
from app.services.projects import ProjectService

router = APIRouter(
    prefix="/research",
    tags=["research"],
    dependencies=[Depends(user_rate_limit)],
    responses={401: ERROR_RESPONSES[401], 429: ERROR_RESPONSES[429]},
)


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    responses={422: ERROR_RESPONSES[422]},
)
async def create_project(
    body: ProjectCreate, user: CurrentUser, session: SessionDep, cfg: SettingsDep
) -> ProjectRead:
    return await ProjectService(session, cfg).create(user.id, body)


@router.get("", response_model=Page[ProjectSummary])
async def list_projects(
    user: CurrentUser,
    session: SessionDep,
    cfg: SettingsDep,
    page: Annotated[PageParams, Depends()],
    status_filter: Annotated[ProjectStatus | None, Query(alias="status")] = None,
) -> Page[ProjectSummary]:
    return await ProjectService(session, cfg).list(
        user.id, limit=page.limit, offset=page.offset, status=status_filter
    )


@router.get("/{project_id}", response_model=ProjectRead, responses={404: ERROR_RESPONSES[404]})
async def get_project(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep, cfg: SettingsDep
) -> ProjectRead:
    return await ProjectService(session, cfg).read(user.id, project_id)
