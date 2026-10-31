import base64
import binascii
import uuid
from decimal import Decimal
from typing import Protocol

from app.core.security import generate_api_key, hash_api_key
from app.db.models import Product, Store
from app.schemas.catalog import ProductCreate, ProductOut, StoreCreate, StoreCreated, StoreOut
from app.schemas.common import Page


class InvalidCursorError(ValueError):
    pass


def encode_cursor(last_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(str(last_id).encode()).decode()


def decode_cursor(cursor: str | None) -> uuid.UUID | None:
    if cursor is None:
        return None
    try:
        return uuid.UUID(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, binascii.Error):
        raise InvalidCursorError("invalid cursor") from None


class StoreRepo(Protocol):
    async def list_for_tenant(
        self, tenant_id: uuid.UUID, limit: int, after: uuid.UUID | None
    ) -> list[Store]: ...
    async def create(
        self, tenant_id: uuid.UUID, name: str, region: str, timezone: str, api_key_hash: str
    ) -> Store: ...


class ProductRepo(Protocol):
    async def list_for_tenant(
        self, tenant_id: uuid.UUID, limit: int, after: uuid.UUID | None
    ) -> list[Product]: ...
    async def create(
        self, tenant_id: uuid.UUID, sku: str, name: str, category: str, list_price: Decimal
    ) -> Product: ...


class CatalogService:
    """Stores and products (tenant-scoped, cursor-paginated)."""

    def __init__(self, stores: StoreRepo, products: ProductRepo) -> None:
        self._stores = stores
        self._products = products

    async def list_stores(
        self, tenant_id: uuid.UUID, limit: int, cursor: str | None
    ) -> Page[StoreOut]:
        rows = await self._stores.list_for_tenant(tenant_id, limit + 1, decode_cursor(cursor))
        page = rows[:limit]
        return Page[StoreOut](
            items=[StoreOut.model_validate(s) for s in page],
            next_cursor=encode_cursor(page[-1].id) if len(rows) > limit else None,
        )

    async def create_store(self, tenant_id: uuid.UUID, body: StoreCreate) -> StoreCreated:
        api_key = generate_api_key()
        store = await self._stores.create(
            tenant_id, body.name, body.region, body.timezone, hash_api_key(api_key)
        )
        return StoreCreated(**StoreOut.model_validate(store).model_dump(), api_key=api_key)

    async def list_products(
        self, tenant_id: uuid.UUID, limit: int, cursor: str | None
    ) -> Page[ProductOut]:
        rows = await self._products.list_for_tenant(tenant_id, limit + 1, decode_cursor(cursor))
        page = rows[:limit]
        return Page[ProductOut](
            items=[ProductOut.model_validate(p) for p in page],
            next_cursor=encode_cursor(page[-1].id) if len(rows) > limit else None,
        )

    async def create_product(self, tenant_id: uuid.UUID, body: ProductCreate) -> Product:
        return await self._products.create(
            tenant_id, body.sku, body.name, body.category, body.list_price
        )
