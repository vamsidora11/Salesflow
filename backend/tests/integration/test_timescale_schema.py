import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
from alembic import command

from tests.integration.conftest import alembic_config

INSERT = (
    "INSERT INTO sales_events (event_time, event_id, product_id, received_at, tenant_id, store_id,"
    " quantity, unit_price, amount, currency, channel, txn)"
    " VALUES ($1,$2,$3,now(),$4,$5,$6,$7,$8,'USD','pos',$9)"
)


async def _insert(
    pg: asyncpg.Connection, when: datetime, tenant: uuid.UUID, store: uuid.UUID, txn: int = 1
) -> None:
    await pg.execute(
        INSERT,
        when,
        uuid.uuid4(),
        uuid.uuid4(),
        tenant,
        store,
        2,
        10,
        20,
        txn,  # noqa: E501
    )


async def test_sales_events_is_hypertable_and_aggregates_exist(pg: asyncpg.Connection) -> None:
    hypertables = {
        r["hypertable_name"]
        for r in await pg.fetch("SELECT hypertable_name FROM timescaledb_information.hypertables")
    }
    aggregates = {
        r["view_name"]
        for r in await pg.fetch(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates"
        )
    }
    assert "sales_events" in hypertables
    assert {"sales_hourly", "sales_daily"} <= aggregates


async def test_policies_are_registered(pg: asyncpg.Connection) -> None:
    rows = await pg.fetch(
        "SELECT proc_name, hypertable_name FROM timescaledb_information.jobs"
        " WHERE proc_name LIKE 'policy_%'"
    )
    registered = {(r["proc_name"], r["hypertable_name"]) for r in rows}
    assert ("policy_retention", "sales_events") in registered
    assert ("policy_compression", "sales_events") in registered
    assert any(p == "policy_refresh_continuous_aggregate" for p, _ in registered)


async def test_insert_appears_in_hourly_after_refresh(pg: asyncpg.Connection) -> None:
    tenant, store = uuid.uuid4(), uuid.uuid4()
    when = datetime.now(UTC) - timedelta(hours=2)
    await _insert(pg, when, tenant, store)
    await pg.execute("CALL refresh_continuous_aggregate('sales_hourly', NULL, NULL)")
    row = await pg.fetchrow(
        "SELECT sum(revenue) AS revenue, sum(units) AS units, sum(transactions) AS txns"
        " FROM sales_hourly WHERE tenant_id = $1",
        tenant,
    )
    assert row is not None
    assert (row["revenue"], row["units"], row["txns"]) == (20, 2, 1)


async def test_duplicate_row_is_ignored_with_on_conflict(pg: asyncpg.Connection) -> None:
    tenant, store, event, product = (uuid.uuid4() for _ in range(4))
    when = datetime.now(UTC) - timedelta(hours=1)
    sql = (
        "INSERT INTO sales_events (event_time, event_id, product_id, received_at, tenant_id,"
        " store_id, quantity, unit_price, amount, currency, channel, txn)"
        " VALUES ($1,$2,$3,now(),$4,$5,1,5,5,'USD','pos',1)"
        " ON CONFLICT (event_time, event_id, product_id) DO NOTHING"
    )
    for _ in range(2):
        await pg.execute(sql, when, event, product, tenant, store)
    count = await pg.fetchval("SELECT count(*) FROM sales_events WHERE event_id = $1", event)
    assert count == 1


async def test_week_range_query_excludes_chunks(pg: asyncpg.Connection) -> None:
    tenant, store = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    for days_ago in range(0, 30, 2):
        await _insert(pg, now - timedelta(days=days_ago), tenant, store)
    start, end = now - timedelta(days=7), now + timedelta(minutes=1)
    plan = "\n".join(
        r[0]
        for r in await pg.fetch(
            "EXPLAIN SELECT sum(amount) FROM sales_events"
            " WHERE tenant_id = $1 AND event_time >= $2 AND event_time < $3",
            tenant,
            start,
            end,
        )
    )
    total_chunks = await pg.fetchval(
        "SELECT count(*) FROM timescaledb_information.chunks WHERE hypertable_name='sales_events'"
    )
    scanned = len({tok for tok in plan.replace("\n", " ").split() if tok.startswith("_hyper_")})
    assert total_chunks >= 10
    assert 0 < scanned < total_chunks, plan


def test_migrations_run_down_and_up_cleanly(fresh_timescale_url: str) -> None:
    cfg = alembic_config(fresh_timescale_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
