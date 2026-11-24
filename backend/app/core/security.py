"""Password hashing (Argon2id) and JWT access tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

_hasher = PasswordHash((Argon2Hasher(),))

# Verified against when the email is unknown, so login takes the same time
# whether or not an account exists (no user-enumeration timing oracle).
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")

TOKEN_TYPE = "access"  # noqa: S105 (token type label, not a secret)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if hashed is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    try:
        return _hasher.verify(password, hashed)
    except Exception:  # malformed hash in DB: treat as failure, never crash login
        return False


class InvalidTokenError(Exception):
    pass


def create_access_token(user_id: uuid.UUID, settings: Settings) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "type": TOKEN_TYPE,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, int(ttl.total_seconds())


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    jti: str
    expires_at: datetime


def decode_token(token: str, settings: Settings) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # pinned: rejects alg=none / alg confusion
            options={"require": ["sub", "exp", "iat", "type", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("type") != TOKEN_TYPE:
        raise InvalidTokenError("wrong token type")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError("malformed subject") from exc
    return TokenClaims(user_id, str(payload["jti"]), datetime.fromtimestamp(payload["exp"], UTC))


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    return decode_token(token, settings).user_id


REVOKED_PREFIX = "revoked-jti:"


async def revoke_token(redis: Redis | None, claims: TokenClaims) -> None:
    """Deny-list a token until it would have expired anyway."""
    if redis is None:
        return
    ttl = int((claims.expires_at - datetime.now(UTC)).total_seconds())
    if ttl > 0:
        await redis.set(f"{REVOKED_PREFIX}{claims.jti}", "1", ex=ttl)


async def is_revoked(redis: Redis | None, jti: str) -> bool:
    """Fails open if Redis is unavailable: tokens are short-lived (default 60 min) and
    availability of every authenticated request outweighs that window. Logged."""
    if redis is None:
        return False
    try:
        return bool(await redis.exists(f"{REVOKED_PREFIX}{jti}"))
    except RedisError:
        log.warning("revocation_check_unavailable")
        return False
