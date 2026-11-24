from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep, auth_rate_limit, user_rate_limit
from app.core.security import create_access_token, revoke_token
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, TokenResponse, UserRead
from app.schemas.common import ERROR_RESPONSES, ErrorResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit)],
    responses={409: {"model": ErrorResponse, "description": "Email already registered"}},
)
async def register(
    body: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> AuthResponse:
    user = await AuthService(session).register(body.email, body.password)
    token, expires_in = create_access_token(user.id, settings)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=TokenResponse(access_token=token, expires_in=expires_in),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit)],
    responses={401: ERROR_RESPONSES[401], 429: ERROR_RESPONSES[429]},
)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    user = await AuthService(session).authenticate(body.email, body.password)
    token, expires_in = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: ERROR_RESPONSES[401]},
)
async def logout(request: Request, user: CurrentUser) -> Response:
    """Revoke the presented token server-side (it stops working immediately)."""
    await revoke_token(getattr(request.app.state, "redis", None), request.state.token_claims)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=UserRead,
    dependencies=[Depends(user_rate_limit)],
    responses={401: ERROR_RESPONSES[401]},
)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
