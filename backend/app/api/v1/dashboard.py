from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, SessionDep, SettingsDep, user_rate_limit
from app.schemas.common import ERROR_RESPONSES
from app.schemas.runs import DashboardRead
from app.services.execution import ExecutionService

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(user_rate_limit)],
    responses={401: ERROR_RESPONSES[401]},
)


@router.get("", response_model=DashboardRead)
async def dashboard(user: CurrentUser, session: SessionDep, cfg: SettingsDep) -> DashboardRead:
    """Aggregates computed from the caller's own database records (no placeholder metrics)."""
    return await ExecutionService(session, cfg).dashboard(user.id)
