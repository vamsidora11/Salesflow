from typing import Protocol

from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.schemas.auth import LoginRequest, TokenResponse

# Used to equalise timing when the e-mail does not exist.
_DUMMY_HASH = hash_password("not-a-real-password")


class InvalidCredentialsError(Exception):
    pass


class UserLookup(Protocol):
    async def get_by_email(self, email: str) -> User | None: ...


class AuthService:
    def __init__(self, users: UserLookup, secret: str, expire_minutes: int) -> None:
        self._users = users
        self._secret = secret
        self._expire_minutes = expire_minutes

    async def login(self, request: LoginRequest) -> TokenResponse:
        user = await self._users.get_by_email(request.email.strip())
        password_ok = verify_password(request.password, user.password_hash if user else _DUMMY_HASH)
        if user is None or not password_ok:
            raise InvalidCredentialsError
        token = create_access_token(
            secret=self._secret,
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            expires_minutes=self._expire_minutes,
        )
        return TokenResponse(
            access_token=token,
            expires_in=self._expire_minutes * 60,
            role=user.role,  # type: ignore[arg-type,unused-ignore]
        )
