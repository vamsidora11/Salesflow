import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import decode_access_token, hash_api_key, hash_password
from app.db.models import Product, Store, User
from app.repositories.products import DuplicateSkuError
from app.services.catalog import CatalogService, InvalidCursorError, decode_cursor, encode_cursor
from tests.conftest import TENANT_A, TENANT_B, auth_headers


def make_store(tenant: uuid.UUID, n: int) -> Store:
    return Store(
        id=uuid.UUID(int=n),
        tenant_id=tenant,
        name=f"Store {n}",
        region="EU",
        timezone="UTC",
        api_key_hash="h",
        is_active=True,
        created_at=datetime.now(UTC),
    )


class FakeStores:
    def __init__(self) -> None:
        self.rows = [make_store(TENANT_A, n) for n in range(1, 6)] + [make_store(TENANT_B, 99)]
        self.created_hash: str | None = None

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, limit: int, after: uuid.UUID | None
    ) -> list[Store]:
        rows = sorted((r for r in self.rows if r.tenant_id == tenant_id), key=lambda r: r.id)
        return [r for r in rows if after is None or r.id > after][:limit]

    async def create(
        self, tenant_id: uuid.UUID, name: str, region: str, timezone: str, api_key_hash: str
    ) -> Store:
        self.created_hash = api_key_hash
        return make_store(tenant_id, 500)


class FakeProducts:
    async def list_for_tenant(
        self, tenant_id: uuid.UUID, limit: int, after: uuid.UUID | None
    ) -> list[Product]:
        return []

    async def create(
        self, tenant_id: uuid.UUID, sku: str, name: str, category: str, list_price: Decimal
    ) -> Product:
        if sku == "DUP":
            raise DuplicateSkuError(sku)
        return Product(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            sku=sku,
            name=name,
            category=category,
            list_price=list_price,
            created_at=datetime.now(UTC),
        )


@pytest.fixture
def stores() -> FakeStores:
    return FakeStores()


@pytest.fixture
def catalog(client: TestClient, stores: FakeStores) -> FakeStores:
    client.app.dependency_overrides[deps.get_catalog_service] = lambda: CatalogService(  # type: ignore[attr-defined]
        stores, FakeProducts()
    )
    return stores


def test_cursor_roundtrip_and_invalid_cursor() -> None:
    uid = uuid.uuid4()
    assert decode_cursor(encode_cursor(uid)) == uid
    assert decode_cursor(None) is None
    with pytest.raises(InvalidCursorError):
        decode_cursor("!!!not-a-cursor")


def test_stores_are_tenant_scoped_and_cursor_paginated(
    client: TestClient, catalog: FakeStores
) -> None:
    first = client.get("/api/v1/stores", params={"limit": 2}, headers=auth_headers("viewer")).json()
    assert [s["name"] for s in first["items"]] == ["Store 1", "Store 2"]
    assert first["next_cursor"]
    second = client.get(
        "/api/v1/stores",
        params={"limit": 2, "cursor": first["next_cursor"]},
        headers=auth_headers(),
    ).json()
    assert [s["name"] for s in second["items"]] == ["Store 3", "Store 4"]
    last = client.get(
        "/api/v1/stores",
        params={"limit": 2, "cursor": second["next_cursor"]},
        headers=auth_headers(),
    ).json()
    assert [s["name"] for s in last["items"]] == ["Store 5"] and last["next_cursor"] is None
    assert "api_key_hash" not in str(first)
    other = client.get("/api/v1/stores", headers=auth_headers(tenant_id=TENANT_B)).json()
    assert [s["name"] for s in other["items"]] == ["Store 99"]


def test_invalid_cursor_is_422(client: TestClient, catalog: FakeStores) -> None:
    r = client.get("/api/v1/stores", params={"cursor": "%%%"}, headers=auth_headers())
    assert r.status_code == 422


def test_create_store_requires_admin_and_returns_key_once(
    client: TestClient, catalog: FakeStores
) -> None:
    body = {"name": "New", "region": "EU"}
    assert (
        client.post("/api/v1/stores", json=body, headers=auth_headers("analyst")).status_code == 403
    )
    response = client.post("/api/v1/stores", json=body, headers=auth_headers("admin"))
    assert response.status_code == 201
    key = response.json()["api_key"]
    assert key.startswith("sf_")
    assert catalog.created_hash == hash_api_key(key)  # only the hash reaches the repository


def test_create_product_conflict_and_validation(client: TestClient, catalog: FakeStores) -> None:
    headers = auth_headers("admin")
    ok = client.post(
        "/api/v1/products",
        json={"sku": "A1", "name": "n", "category": "c", "list_price": "9.99"},
        headers=headers,
    )
    assert ok.status_code == 201 and ok.json()["list_price"] == "9.99"
    dup = client.post(
        "/api/v1/products",
        json={"sku": "DUP", "name": "n", "category": "c", "list_price": "1.00"},
        headers=headers,
    )
    assert dup.status_code == 409
    bad = client.post(
        "/api/v1/products",
        json={"sku": "B", "name": "n", "category": "c", "list_price": "-1"},
        headers=headers,
    )
    assert bad.status_code == 422


# --- login --------------------------------------------------------------------------------------


class FakeUsers:
    def __init__(self, session: Any) -> None:
        self.user = User(
            id=uuid.uuid4(),
            tenant_id=TENANT_A,
            email="analyst@acme.example",
            password_hash=hash_password("correct horse"),
            role="analyst",
        )

    async def get_by_email(self, email: str) -> User | None:
        return self.user if email.lower() == self.user.email else None


@pytest.fixture
def login_wired(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "UserRepository", FakeUsers)
    client.app.dependency_overrides[deps.get_session] = lambda: object()  # type: ignore[attr-defined]


def test_login_success_returns_a_working_token(client: TestClient, login_wired: None) -> None:
    r = client.post(
        "/api/v1/auth/login", json={"email": "analyst@acme.example", "password": "correct horse"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "analyst" and body["token_type"] == "bearer" and body["expires_in"] > 0
    claims = decode_access_token(body["access_token"], client.app.state.settings.jwt_secret)  # type: ignore[attr-defined]
    assert claims["role"] == "analyst" and claims["tenant_id"] == str(TENANT_A)


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "analyst@acme.example", "password": "wrong"},
        {"email": "nobody@acme.example", "password": "correct horse"},
    ],
)
def test_login_failures_are_indistinguishable_401(
    client: TestClient, login_wired: None, payload: dict[str, str]
) -> None:
    r = client.post("/api/v1/auth/login", json=payload)
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid email or password"}
