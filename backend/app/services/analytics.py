import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from app.core.config import Settings
from app.repositories.analytics import (
    DimensionRow,
    SummaryRow,
    TimeseriesRow,
    TopProductRow,
)
from app.repositories.filters import AggregateFilters
from app.schemas.analytics import (
    BreakdownResponse,
    DimensionBreakdown,
    Meta,
    SummaryResponse,
    TimeseriesPoint,
    TimeseriesResponse,
    TopProduct,
    TopProductsResponse,
)

CENT = Decimal("0.01")


class InvalidRangeError(ValueError):
    """The requested time range is missing, inverted, or exceeds the cap."""


class AnalyticsReader(Protocol):
    async def summary(self, f: AggregateFilters) -> SummaryRow: ...
    async def timeseries(
        self,
        f: AggregateFilters,
        granularity: Literal["hour", "day"],
        metric: Literal["revenue", "units"],
    ) -> list[TimeseriesRow]: ...
    async def top_products(self, f: AggregateFilters, limit: int) -> list[TopProductRow]: ...
    async def by_region(self, f: AggregateFilters) -> list[DimensionRow]: ...
    async def by_channel(self, f: AggregateFilters) -> list[DimensionRow]: ...


class FreshnessSource(Protocol):
    async def age_seconds(self, tenant_id: uuid.UUID) -> float | None: ...


class CurrencySource(Protocol):
    async def tenant_currency(self, tenant_id: uuid.UUID) -> str: ...


def as_utc(value: datetime) -> datetime:
    """Naive timestamps are interpreted as UTC (documented in the API)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_range(
    start: datetime, end: datetime, granularity: Literal["hour", "day"], settings: Settings
) -> tuple[datetime, datetime]:
    start, end = as_utc(start), as_utc(end)
    if end <= start:
        raise InvalidRangeError("`to` must be after `from`")
    max_days = (
        settings.hourly_max_range_days if granularity == "hour" else settings.daily_max_range_days
    )
    if end - start > timedelta(days=max_days):
        raise InvalidRangeError(
            f"time range exceeds the {max_days}-day maximum for {granularity} data"
        )
    return start, end


class AnalyticsService:
    def __init__(
        self,
        repo: AnalyticsReader,
        freshness: FreshnessSource,
        currencies: CurrencySource,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._freshness = freshness
        self._currencies = currencies
        self._settings = settings

    async def _meta(self, tenant_id: uuid.UUID) -> Meta:
        return Meta(
            generated_at=datetime.now(UTC),
            data_freshness_seconds=await self._freshness.age_seconds(tenant_id),
            currency=await self._currencies.tenant_currency(tenant_id),
        )

    def _filters(
        self,
        tenant_id: uuid.UUID,
        start: datetime,
        end: datetime,
        granularity: Literal["hour", "day"],
        store_id: uuid.UUID | None,
        region: str | None,
        channel: str | None,
    ) -> AggregateFilters:
        start, end = validate_range(start, end, granularity, self._settings)
        return AggregateFilters(tenant_id, start, end, store_id, region, channel)

    async def summary(
        self,
        tenant_id: uuid.UUID,
        start: datetime,
        end: datetime,
        store_id: uuid.UUID | None = None,
        region: str | None = None,
        channel: str | None = None,
    ) -> SummaryResponse:
        f = self._filters(tenant_id, start, end, "day", store_id, region, channel)
        row = await self._repo.summary(f)
        aov = (
            (row.revenue / row.transactions).quantize(CENT) if row.transactions else Decimal("0.00")
        )
        return SummaryResponse(
            revenue=row.revenue,
            units=row.units,
            transactions=row.transactions,
            average_order_value=aov,
            meta=await self._meta(tenant_id),
        )

    async def timeseries(
        self,
        tenant_id: uuid.UUID,
        granularity: Literal["hour", "day"],
        metric: Literal["revenue", "units"],
        start: datetime,
        end: datetime,
        store_id: uuid.UUID | None = None,
        region: str | None = None,
        channel: str | None = None,
    ) -> TimeseriesResponse:
        f = self._filters(tenant_id, start, end, granularity, store_id, region, channel)
        rows = await self._repo.timeseries(f, granularity, metric)
        return TimeseriesResponse(
            granularity=granularity,
            metric=metric,
            points=[TimeseriesPoint(bucket=r.bucket, value=r.value) for r in rows],
            meta=await self._meta(tenant_id),
        )

    async def top_products(
        self,
        tenant_id: uuid.UUID,
        limit: int,
        start: datetime,
        end: datetime,
        store_id: uuid.UUID | None = None,
        region: str | None = None,
        channel: str | None = None,
    ) -> TopProductsResponse:
        f = self._filters(tenant_id, start, end, "day", store_id, region, channel)
        rows = await self._repo.top_products(f, limit)
        return TopProductsResponse(
            items=[
                TopProduct(
                    product_id=uuid.UUID(r.product_id),
                    sku=r.sku,
                    name=r.name,
                    revenue=r.revenue,
                    units=r.units,
                )
                for r in rows
            ],
            meta=await self._meta(tenant_id),
        )

    async def by_region(
        self,
        tenant_id: uuid.UUID,
        start: datetime,
        end: datetime,
        store_id: uuid.UUID | None = None,
        channel: str | None = None,
    ) -> BreakdownResponse:
        f = self._filters(tenant_id, start, end, "day", store_id, None, channel)
        return await self._breakdown(tenant_id, await self._repo.by_region(f))

    async def by_channel(
        self,
        tenant_id: uuid.UUID,
        start: datetime,
        end: datetime,
        store_id: uuid.UUID | None = None,
        region: str | None = None,
    ) -> BreakdownResponse:
        f = self._filters(tenant_id, start, end, "hour", store_id, region, None)
        return await self._breakdown(tenant_id, await self._repo.by_channel(f))

    async def _breakdown(self, tenant_id: uuid.UUID, rows: list[DimensionRow]) -> BreakdownResponse:
        return BreakdownResponse(
            items=[
                DimensionBreakdown(
                    label=r.label, revenue=r.revenue, units=r.units, transactions=r.transactions
                )
                for r in rows
            ],
            meta=await self._meta(tenant_id),
        )
