from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.core.rate_limit import client_ip, enforce_rate_limit
from app.core.security import InvalidTokenError, decode_token, is_revoked
from app.database.session import get_db_session
from app.models import User
from app.repositories.users import UserRepository

_bearer = HTTPBearer(auto_error=False, description="JWT from /api/v1/auth/login")

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise AuthenticationError()
    try:
        claims = decode_token(creds.credentials, settings)
    except InvalidTokenError as exc:
        raise AuthenticationError("Invalid or expired token") from exc
    if await is_revoked(getattr(request.app.state, "redis", None), claims.jti):
        raise AuthenticationError("Invalid or expired token")
    request.state.token_claims = claims
    user = await UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired token")
    request.state.user_id = user.id
    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def user_rate_limit(request: Request, user: CurrentUser, settings: SettingsDep) -> None:
    """General per-user limit applied to every authenticated route."""
    await enforce_rate_limit(
        request, f"api:user:{user.id}", settings.rate_limit_per_minute, window_seconds=60
    )


async def auth_rate_limit(request: Request, settings: SettingsDep) -> None:
    """Per-IP limit on login/register to slow credential stuffing."""
    await enforce_rate_limit(
        request, f"auth:ip:{client_ip(request)}", max(5, settings.rate_limit_per_minute // 6), 60
    )
