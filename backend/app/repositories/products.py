import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product


class DuplicateSkuError(Exception):
    pass


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, limit: int, after: uuid.UUID | None
    ) -> list[Product]:
        stmt = (
            select(Product).where(Product.tenant_id == tenant_id).order_by(Product.id).limit(limit)
        )
        if after is not None:
            stmt = stmt.where(Product.id > after)
        return list((await self._session.scalars(stmt)).all())

    async def create(
        self, tenant_id: uuid.UUID, sku: str, name: str, category: str, list_price: Decimal
    ) -> Product:
        product = Product(
            tenant_id=tenant_id, sku=sku, name=name, category=category, list_price=list_price
        )
        self._session.add(product)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DuplicateSkuError(sku) from exc
        return product
