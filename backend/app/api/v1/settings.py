from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, SessionDep, SettingsDep, user_rate_limit
from app.schemas.common import ERROR_RESPONSES
from app.schemas.settings import SettingsRead, SettingsUpdate
from app.services.settings import SettingsService

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(user_rate_limit)],
    responses={401: ERROR_RESPONSES[401], 429: ERROR_RESPONSES[429]},
)


@router.get("", response_model=SettingsRead)
async def get_settings_endpoint(
    user: CurrentUser, session: SessionDep, cfg: SettingsDep
) -> SettingsRead:
    return await SettingsService(session, cfg).get(user.id)


@router.put("", response_model=SettingsRead, responses={422: ERROR_RESPONSES[422]})
async def update_settings(
    body: SettingsUpdate, user: CurrentUser, session: SessionDep, cfg: SettingsDep
) -> SettingsRead:
    return await SettingsService(session, cfg).update(user.id, body)
