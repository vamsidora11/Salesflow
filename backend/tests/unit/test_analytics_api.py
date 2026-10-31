import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.repositories.analytics import (
    DimensionRow,
    SummaryRow,
    TimeseriesRow,
    TopProductRow,
)
from app.repositories.filters import AggregateFilters
from app.services.analytics import AnalyticsService
from tests.conftest import TENANT_A, TENANT_B, auth_headers

PRODUCT_ID = str(uuid.uuid4())


class FakeAnalyticsRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AggregateFilters]] = []

    async def summary(self, f: AggregateFilters) -> SummaryRow:
        self.calls.append(("summary", f))
        return SummaryRow(Decimal("1000.00"), 80, 8)

    async def timeseries(
        self, f: AggregateFilters, granularity: Any, metric: Any
    ) -> list[TimeseriesRow]:
        self.calls.append(("timeseries", f))
        return [TimeseriesRow(datetime(2026, 10, 30, 14, tzinfo=UTC), Decimal("12.50"))]

    async def top_products(self, f: AggregateFilters, limit: int) -> list[TopProductRow]:
        self.calls.append(("top_products", f))
        return [TopProductRow(PRODUCT_ID, "SKU-1", "Widget", Decimal("99.90"), 3)]

    async def by_region(self, f: AggregateFilters) -> list[DimensionRow]:
        self.calls.append(("by_region", f))
        return [DimensionRow("Europe", Decimal("10.00"), 1, 1)]

    async def by_channel(self, f: AggregateFilters) -> list[DimensionRow]:
        self.calls.append(("by_channel", f))
        return [DimensionRow("pos", Decimal("10.00"), 1, 1)]


class FakeFreshness:
    async def age_seconds(self, tenant_id: uuid.UUID) -> float | None:
        return 2.5


class FakeCurrencies:
    async def tenant_currency(self, tenant_id: uuid.UUID) -> str:
        return "EUR"


@pytest.fixture
def repo(client: TestClient) -> FakeAnalyticsRepo:
    fake = FakeAnalyticsRepo()
    settings = client.app.state.settings  # type: ignore[attr-defined]
    client.app.dependency_overrides[deps.get_analytics_service] = lambda: AnalyticsService(  # type: ignore[attr-defined]
        fake, FakeFreshness(), FakeCurrencies(), settings
    )
    return fake


RANGE = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-08T00:00:00Z"}
ENDPOINTS = ["summary", "timeseries", "top-products", "by-region", "by-channel"]


def get(client: TestClient, path: str, role: str = "analyst", **params: Any) -> Any:
    return client.get(
        f"/api/v1/analytics/{path}", params={**RANGE, **params}, headers=auth_headers(role)
    )


def test_summary_returns_kpis_with_money_as_strings_and_meta(
    client: TestClient, repo: FakeAnalyticsRepo
) -> None:
    body = get(client, "summary").json()
    assert body["revenue"] == "1000.00"
    assert body["units"] == 80 and body["transactions"] == 8
    assert body["average_order_value"] == "125.00"
    assert body["meta"]["currency"] == "EUR"
    assert body["meta"]["data_freshness_seconds"] == 2.5
    assert "generated_at" in body["meta"]


def test_average_order_value_is_zero_without_transactions(client: TestClient) -> None:
    class Empty(FakeAnalyticsRepo):
        async def summary(self, f: AggregateFilters) -> SummaryRow:
            return SummaryRow(Decimal(0), 0, 0)

    settings = client.app.state.settings  # type: ignore[attr-defined]
    client.app.dependency_overrides[deps.get_analytics_service] = lambda: AnalyticsService(  # type: ignore[attr-defined]
        Empty(), FakeFreshness(), FakeCurrencies(), settings
    )
    assert get(client, "summary").json()["average_order_value"] == "0.00"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_time_range_is_required(client: TestClient, repo: FakeAnalyticsRepo, path: str) -> None:
    response = client.get(f"/api/v1/analytics/{path}", headers=auth_headers())
    assert response.status_code == 422


@pytest.mark.parametrize("path", ENDPOINTS)
def test_inverted_range_is_422(client: TestClient, repo: FakeAnalyticsRepo, path: str) -> None:
    response = get(client, path, **{"from": RANGE["to"], "to": RANGE["from"]})
    assert response.status_code == 422
    assert "after" in response.json()["detail"]


def test_hourly_range_is_capped_at_366_days(client: TestClient, repo: FakeAnalyticsRepo) -> None:
    too_long = {"from": "2025-01-01T00:00:00Z", "to": "2026-06-01T00:00:00Z"}
    response = get(client, "timeseries", granularity="hour", **too_long)
    assert response.status_code == 422
    assert "366" in response.json()["detail"]
    ok = get(client, "timeseries", granularity="day", **too_long)
    assert ok.status_code == 200  # daily data allows longer ranges


def test_naive_timestamps_are_read_as_utc(client: TestClient, repo: FakeAnalyticsRepo) -> None:
    get(client, "summary", **{"from": "2026-10-01T00:00:00", "to": "2026-10-02T00:00:00"})
    _, f = repo.calls[0]
    assert f.start.tzinfo is not None and f.start.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_tenant_comes_from_the_token_never_from_the_request(
    client: TestClient, repo: FakeAnalyticsRepo
) -> None:
    get(client, "summary", tenant_id=str(TENANT_B))  # extra param is ignored
    response = client.get(
        "/api/v1/analytics/summary", params=RANGE, headers=auth_headers("analyst", TENANT_B)
    )
    assert response.status_code == 200
    assert [f.tenant_id for _, f in repo.calls] == [TENANT_A, TENANT_B]


def test_filters_are_passed_through(client: TestClient, repo: FakeAnalyticsRepo) -> None:
    store = str(uuid.uuid4())
    get(client, "timeseries", store_id=store, region="Europe", channel="pos")
    _, f = repo.calls[0]
    assert str(f.store_id) == store and f.region == "Europe" and f.channel == "pos"


def test_invalid_channel_is_422(client: TestClient, repo: FakeAnalyticsRepo) -> None:
    assert get(client, "timeseries", channel="fax").status_code == 422


def test_timeseries_top_products_and_breakdowns_shapes(
    client: TestClient, repo: FakeAnalyticsRepo
) -> None:
    ts = get(client, "timeseries", granularity="hour", metric="revenue").json()
    assert ts["points"] == [{"bucket": "2026-10-30T14:00:00Z", "value": "12.50"}]
    top = get(client, "top-products", limit=5).json()
    assert top["items"][0]["sku"] == "SKU-1" and top["items"][0]["revenue"] == "99.90"
    assert get(client, "by-region").json()["items"][0]["label"] == "Europe"
    assert get(client, "by-channel").json()["items"][0]["label"] == "pos"


def test_top_products_limit_bounds(client: TestClient, repo: FakeAnalyticsRepo) -> None:
    assert get(client, "top-products", limit=0).status_code == 422
    assert get(client, "top-products", limit=101).status_code == 422


# --- authentication / roles -------------------------------------------------------------------


@pytest.mark.parametrize("path", ENDPOINTS)
def test_unauthenticated_is_401(client: TestClient, repo: FakeAnalyticsRepo, path: str) -> None:
    assert client.get(f"/api/v1/analytics/{path}", params=RANGE).status_code == 401


def test_garbage_and_wrong_secret_tokens_are_401(
    client: TestClient, repo: FakeAnalyticsRepo
) -> None:
    from tests.conftest import token_for

    for token in ("garbage", token_for(secret="another-secret-that-is-long-enough-xx")):
        r = client.get(
            "/api/v1/analytics/summary", params=RANGE, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 401


def test_viewer_can_read_summary_only(client: TestClient, repo: FakeAnalyticsRepo) -> None:
    assert get(client, "summary", role="viewer").status_code == 200
    for path in ("timeseries", "top-products", "by-region", "by-channel"):
        assert get(client, path, role="viewer").status_code == 403


def test_admin_has_full_access(client: TestClient, repo: FakeAnalyticsRepo) -> None:
    for path in ENDPOINTS:
        assert get(client, path, role="admin").status_code == 200
