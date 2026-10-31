from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.filters import (
    AGGREGATE_TABLES,
    METRIC_COLUMNS,
    AggregateFilters,
    build_where,
)


@dataclass(frozen=True)
class SummaryRow:
    revenue: Decimal
    units: int
    transactions: int


@dataclass(frozen=True)
class TimeseriesRow:
    bucket: datetime
    value: Decimal


@dataclass(frozen=True)
class TopProductRow:
    product_id: str
    sku: str
    name: str
    revenue: Decimal
    units: int


@dataclass(frozen=True)
class DimensionRow:
    label: str
    revenue: Decimal
    units: int
    transactions: int


class AnalyticsRepository:
    """Reads continuous aggregates only (never raw events) so queries stay fast."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summary(self, f: AggregateFilters) -> SummaryRow:
        where, params = build_where("d", f)
        row = (
            await self._session.execute(
                text(
                    "SELECT coalesce(sum(d.revenue), 0) AS revenue,"
                    " coalesce(sum(d.units), 0) AS units,"
                    " coalesce(sum(d.transactions), 0) AS transactions"
                    f" FROM sales_daily d WHERE {where}"
                ),
                params,
            )
        ).one()
        return SummaryRow(Decimal(row.revenue), int(row.units), int(row.transactions))

    async def timeseries(
        self,
        f: AggregateFilters,
        granularity: Literal["hour", "day"],
        metric: Literal["revenue", "units"],
    ) -> list[TimeseriesRow]:
        table = AGGREGATE_TABLES[granularity]
        column = METRIC_COLUMNS[metric]
        where, params = build_where("t", f)
        result = await self._session.execute(
            text(
                f"SELECT t.bucket AS bucket, sum(t.{column}) AS value"
                f" FROM {table} t WHERE {where} GROUP BY t.bucket ORDER BY t.bucket"
            ),
            params,
        )
        return [TimeseriesRow(r.bucket, Decimal(r.value)) for r in result]

    async def top_products(self, f: AggregateFilters, limit: int) -> list[TopProductRow]:
        where, params = build_where("d", f)
        result = await self._session.execute(
            text(
                "SELECT p.id AS product_id, p.sku AS sku, p.name AS name,"
                " sum(d.revenue) AS revenue, sum(d.units) AS units"
                " FROM sales_daily d"
                " JOIN products p ON p.id = d.product_id AND p.tenant_id = d.tenant_id"
                f" WHERE {where}"
                " GROUP BY p.id, p.sku, p.name ORDER BY revenue DESC, p.sku LIMIT :limit"
            ),
            {**params, "limit": limit},
        )
        return [
            TopProductRow(str(r.product_id), r.sku, r.name, Decimal(r.revenue), int(r.units))
            for r in result
        ]

    async def by_region(self, f: AggregateFilters) -> list[DimensionRow]:
        where, params = build_where("d", f)
        result = await self._session.execute(
            text(
                "SELECT s.region AS label, sum(d.revenue) AS revenue, sum(d.units) AS units,"
                " sum(d.transactions) AS transactions"
                " FROM sales_daily d"
                " JOIN stores s ON s.id = d.store_id AND s.tenant_id = d.tenant_id"
                f" WHERE {where} GROUP BY s.region ORDER BY revenue DESC"
            ),
            params,
        )
        return [
            DimensionRow(r.label, Decimal(r.revenue), int(r.units), int(r.transactions))
            for r in result
        ]

    async def by_channel(self, f: AggregateFilters) -> list[DimensionRow]:
        where, params = build_where("h", f)
        result = await self._session.execute(
            text(
                "SELECT h.channel AS label, sum(h.revenue) AS revenue, sum(h.units) AS units,"
                " sum(h.transactions) AS transactions"
                f" FROM sales_hourly h WHERE {where} GROUP BY h.channel ORDER BY revenue DESC"
            ),
            params,
        )
        return [
            DimensionRow(r.label, Decimal(r.revenue), int(r.units), int(r.transactions))
            for r in result
        ]
