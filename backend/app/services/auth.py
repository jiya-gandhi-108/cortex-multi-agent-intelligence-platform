from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ConflictError
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.models import User
from app.repositories.users import UserRepository

log = get_logger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(self, email: str, password: str) -> User:
        if await self.users.get_by_email(email):
            raise ConflictError("An account with this email already exists")
        try:
            async with self.session.begin_nested():
                user = await self.users.create(email, hash_password(password))
        except IntegrityError as exc:  # concurrent registration race
            raise ConflictError("An account with this email already exists") from exc
        log.info("user_registered", user_id=str(user.id))
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.users.get_by_email(email)
        # verify_password runs a dummy hash when user is None (constant-ish time).
        valid = verify_password(password, user.hashed_password if user else None)
        if not user or not valid or not user.is_active:
            log.info("login_failed")
            raise AuthenticationError("Invalid email or password")
        log.info("login_succeeded", user_id=str(user.id))
        return user
