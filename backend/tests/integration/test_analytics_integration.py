"""Analytics values must equal a direct SQL aggregation over raw events, and stay tenant-scoped."""

from datetime import timedelta
from decimal import Decimal

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.analytics import AnalyticsRepository
from app.repositories.filters import AggregateFilters
from app.repositories.sales_events import SalesEventRepository
from tests.integration.helpers import midnight, sale_rows, seed_world


async def refresh(pg: asyncpg.Connection) -> None:
    await pg.execute("CALL refresh_continuous_aggregate('sales_hourly', NULL, NULL)")
    await pg.execute("CALL refresh_continuous_aggregate('sales_daily', NULL, NULL)")


async def test_summary_timeseries_and_breakdowns_match_raw_sql(
    session_factory: async_sessionmaker[AsyncSession], pg: asyncpg.Connection
) -> None:
    world = await seed_world(session_factory)
    other = await seed_world(session_factory)  # a second tenant that must never leak in
    rows = []
    for day in (3, 2, 1):
        for hour in (9, 14):
            when = midnight(day) + timedelta(hours=hour)
            rows += sale_rows(world, when, "Europe", "pos", (2, 1), "10.00")
            rows += sale_rows(world, when, "Asia", "online", (3,), "7.50")
    rows += sale_rows(other, midnight(2) + timedelta(hours=9), "Europe", "pos", (9,), "99.00")

    async with session_factory() as session, session.begin():
        repo = SalesEventRepository(session)
        assert len(await repo.insert_rows(rows)) == len(rows)
        # Idempotent: the same rows again insert nothing.
        assert await repo.insert_rows(rows) == set()
    await refresh(pg)

    start, end = midnight(3), midnight(0)
    f = AggregateFilters(world.tenant_id, start, end)
    raw = await pg.fetchrow(
        "SELECT sum(amount) AS revenue, sum(quantity) AS units, sum(txn) AS txns"
        " FROM sales_events WHERE tenant_id=$1 AND event_time >= $2 AND event_time < $3",
        world.tenant_id,
        start,
        end,
    )
    assert raw is not None

    async with session_factory() as session:
        repo_a = AnalyticsRepository(session)
        summary = await repo_a.summary(f)
        assert (summary.revenue, summary.units, summary.transactions) == (
            raw["revenue"],
            raw["units"],
            raw["txns"],
        )
        assert summary.transactions == 12  # events, not line items

        daily = await repo_a.timeseries(f, "day", "revenue")
        raw_daily = await pg.fetch(
            "SELECT time_bucket('1 day', event_time) AS b, sum(amount) AS v FROM sales_events"
            " WHERE tenant_id=$1 AND event_time >= $2 AND event_time < $3 GROUP BY b ORDER BY b",
            world.tenant_id,
            start,
            end,
        )
        assert [(r.bucket, r.value) for r in daily] == [(r["b"], r["v"]) for r in raw_daily]

        hourly = await repo_a.timeseries(f, "hour", "units")
        raw_hourly = await pg.fetch(
            "SELECT time_bucket('1 hour', event_time) AS b, sum(quantity) AS v FROM sales_events"
            " WHERE tenant_id=$1 AND event_time >= $2 AND event_time < $3 GROUP BY b ORDER BY b",
            world.tenant_id,
            start,
            end,
        )
        assert [(r.bucket, r.value) for r in hourly] == [
            (r["b"], Decimal(r["v"])) for r in raw_hourly
        ]

        regions = {r.label: r.revenue for r in await repo_a.by_region(f)}
        assert regions == {"Europe": Decimal("180.00"), "Asia": Decimal("135.00")}
        channels = {r.label for r in await repo_a.by_channel(f)}
        assert channels == {"pos", "online"}
        top = await repo_a.top_products(f, 2)
        assert len(top) == 2 and top[0].revenue >= top[1].revenue

        # Filters narrow results.
        eu = await repo_a.summary(AggregateFilters(world.tenant_id, start, end, region="Europe"))
        assert eu.revenue == Decimal("180.00")
        online = await repo_a.summary(
            AggregateFilters(world.tenant_id, start, end, channel="online")
        )
        assert online.revenue == Decimal("135.00")

        # Tenant isolation: the other tenant sees only its own sale.
        theirs = await AnalyticsRepository(session).summary(
            AggregateFilters(other.tenant_id, start, end)
        )
        assert theirs.revenue == Decimal("891.00")
