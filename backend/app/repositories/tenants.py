import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[tuple[uuid.UUID, str, str]]:
        """(id, name, currency). Used by system jobs that run for every tenant."""
        rows = await self._session.execute(select(Tenant.id, Tenant.name, Tenant.currency))
        return [(r.id, r.name, r.currency) for r in rows]
