import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.db.models import Product, Store, Tenant, User


@dataclass
class World:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    stores: dict[str, uuid.UUID] = field(default_factory=dict)  # region -> store id
    products: list[uuid.UUID] = field(default_factory=list)


async def seed_world(
    factory: async_sessionmaker[AsyncSession], regions: tuple[str, ...] = ("Europe", "Asia")
) -> World:
    async with factory() as session, session.begin():
        tenant = Tenant(name=f"t-{uuid.uuid4().hex[:6]}", currency="USD")
        session.add(tenant)
        await session.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"{uuid.uuid4().hex[:8]}@example.test",
            password_hash=hash_password("x"),
            role="admin",
        )
        session.add(user)
        world = World(tenant.id, user.id)
        for region in regions:
            store = Store(
                tenant_id=tenant.id,
                name=f"{region} store",
                region=region,
                api_key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            )
            session.add(store)
            await session.flush()
            world.stores[region] = store.id
        for i in range(3):
            product = Product(
                tenant_id=tenant.id,
                sku=f"P{i}-{uuid.uuid4().hex[:4]}",
                name=f"Product {i}",
                category="c",
                list_price=Decimal("10.00"),
            )
            session.add(product)
            await session.flush()
            world.products.append(product.id)
    return world


def sale_rows(
    world: World,
    when: datetime,
    region: str = "Europe",
    channel: str = "pos",
    quantities: tuple[int, ...] = (2, 1),
    price: str = "10.00",
) -> list[dict[str, Any]]:
    """Rows for one transaction (one event) with one line item per quantity."""
    event_id = uuid.uuid4()
    return [
        {
            "event_time": when,
            "event_id": event_id,
            "product_id": world.products[i],
            "received_at": when,
            "tenant_id": world.tenant_id,
            "store_id": world.stores[region],
            "quantity": q,
            "unit_price": Decimal(price),
            "amount": (Decimal(price) * q).quantize(Decimal("0.01")),
            "currency": "USD",
            "channel": channel,
            "txn": 1 if i == 0 else 0,
            "request_id": "req-test",
        }
        for i, q in enumerate(quantities)
    ]


def midnight(days_ago: int) -> datetime:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days_ago)
