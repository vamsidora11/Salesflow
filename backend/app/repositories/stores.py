import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Store


@dataclass(frozen=True)
class StoreIdentity:
    """What an API key resolves to."""

    store_id: uuid.UUID
    tenant_id: uuid.UUID
    region: str


class StoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_api_key_hash(self, key_hash: str) -> StoreIdentity | None:
        row = (
            await self._session.execute(
                select(Store.id, Store.tenant_id, Store.region).where(
                    Store.api_key_hash == key_hash, Store.is_active.is_(True)
                )
            )
        ).first()
        return StoreIdentity(row.id, row.tenant_id, row.region) if row else None

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, limit: int, after: uuid.UUID | None
    ) -> list[Store]:
        stmt = select(Store).where(Store.tenant_id == tenant_id).order_by(Store.id).limit(limit)
        if after is not None:
            stmt = stmt.where(Store.id > after)
        return list((await self._session.scalars(stmt)).all())

    async def create(
        self, tenant_id: uuid.UUID, name: str, region: str, timezone: str, api_key_hash: str
    ) -> Store:
        store = Store(
            tenant_id=tenant_id,
            name=name,
            region=region,
            timezone=timezone,
            api_key_hash=api_key_hash,
        )
        self._session.add(store)
        await self._session.commit()
        return store
