from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import APIModel


class RegisterRequest(APIModel):
    email: EmailStr
    # Upper bound stops multi-megabyte "passwords" being fed to Argon2 (DoS).
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if v.strip() != v:
            raise ValueError("password must not start or end with whitespace")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("password must contain at least one letter and one digit")
        return v


class LoginRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class TokenResponse(APIModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth2 token type label)
    expires_in: int


class UserRead(APIModel):
    id: uuid.UUID
    email: str
    created_at: datetime


class AuthResponse(APIModel):
    user: UserRead
    token: TokenResponse
