from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        return await self._session.scalar(select(User).where(User.email == email.lower()))

    async def tenant_currency(self, tenant_id: object) -> str:
        currency = await self._session.scalar(select(Tenant.currency).where(Tenant.id == tenant_id))
        return currency or "USD"
