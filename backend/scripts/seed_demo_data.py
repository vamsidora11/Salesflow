"""Seed two tenants, stores in different regions, 50 products per tenant, and demo users.

Writes plaintext API keys and demo passwords to seed-output/seed_output.json (git-ignored; override
with SEED_OUTPUT) exactly once;
only hashes are stored in the database. Re-running requires --reset.

    python scripts/seed_demo_data.py [--reset]
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.core.security import generate_api_key, hash_api_key, hash_password
from app.db.models import ExportJob, Product, Store, Tenant, User
from app.db.session import make_engine, make_session_factory

OUTPUT = Path(
    os.environ.get("SEED_OUTPUT")
    or Path(__file__).resolve().parents[2] / "seed-output" / "seed_output.json"
)

TENANTS = {
    "Acme Retail": ["North America", "Europe"],
    "Globex Stores": ["Asia Pacific", "Latin America"],
}
CATEGORIES = ["Beverages", "Snacks", "Household", "Electronics", "Apparel"]


def build_products(tenant_id: Any) -> list[Product]:
    products = []
    for i in range(50):
        category = CATEGORIES[i % len(CATEGORIES)]
        price = Decimal(f"{2 + (i * 7) % 90}.{(i * 13) % 100:02d}")
        products.append(
            Product(
                tenant_id=tenant_id,
                sku=f"SKU-{i + 1:04d}",
                name=f"{category} item {i + 1}",
                category=category,
                list_price=price,
            )
        )
    return products


async def seed(reset: bool) -> dict[str, Any]:
    settings = get_settings()
    engine = make_engine(settings.database_url, null_pool=True)
    factory = make_session_factory(engine)
    output: dict[str, Any] = {"tenants": []}
    async with factory() as session, session.begin():
        existing = await session.scalar(select(func.count()).select_from(Tenant))
        if existing and not reset:
            raise SystemExit("Database already seeded; pass --reset to wipe reference data.")
        if reset:
            # Raw events are not touched; delete them separately if needed.
            for model in (ExportJob, User, Product, Store, Tenant):
                await session.execute(delete(model))

        for tenant_name, regions in TENANTS.items():
            tenant = Tenant(name=tenant_name, currency="USD")
            session.add(tenant)
            await session.flush()

            password = secrets.token_urlsafe(12)
            users = [
                User(
                    tenant_id=tenant.id,
                    email=f"{role}@{tenant_name.split()[0].lower()}.example",
                    password_hash=hash_password(password),
                    role=role,
                )
                for role in ("admin", "analyst", "viewer")
            ]
            session.add_all(users)

            stores_out = []
            for region in regions:
                for n in (1, 2):
                    api_key = generate_api_key()
                    store = Store(
                        tenant_id=tenant.id,
                        name=f"{region} store {n}",
                        region=region,
                        api_key_hash=hash_api_key(api_key),
                    )
                    session.add(store)
                    await session.flush()
                    stores_out.append(
                        {
                            "id": str(store.id),
                            "region": region,
                            "name": store.name,
                            "api_key": api_key,
                        }
                    )

            products = build_products(tenant.id)
            session.add_all(products)
            await session.flush()
            output["tenants"].append(
                {
                    "id": str(tenant.id),
                    "name": tenant_name,
                    "users": [{"email": u.email, "role": u.role} for u in users],
                    "password": password,
                    "stores": stores_out,
                    "products": [
                        {"id": str(p.id), "sku": p.sku, "price": str(p.list_price)}
                        for p in products
                    ],
                }
            )
    await engine.dispose()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    output = asyncio.run(seed(args.reset))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Seeded {len(output['tenants'])} tenants. Credentials written to {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
