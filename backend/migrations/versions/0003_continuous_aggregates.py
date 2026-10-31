"""Continuous aggregates sales_hourly and sales_daily with refresh, compression, retention

Revision ID: 0003
Revises: 0002

Deviations from the plan's sketch (documented in README):
- `channel` is part of the grouping so /analytics/by-channel can read the aggregate.
- `transactions` is sum(txn), i.e. distinct events, not count(*) of line items.
- `materialized_only = false` keeps real-time aggregation on (off by default in TimescaleDB 2.13+).
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

HOURLY = """
CREATE MATERIALIZED VIEW sales_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', event_time) AS bucket,
       tenant_id, store_id, product_id, channel,
       sum(amount)   AS revenue,
       sum(quantity) AS units,
       sum(txn)      AS transactions
FROM sales_events
GROUP BY time_bucket('1 hour', event_time), tenant_id, store_id, product_id, channel
WITH NO DATA
"""

# Hierarchical aggregate: rolls up sales_hourly rather than scanning raw events.
# GROUP BY repeats the expression because `bucket` is also an input column name.
DAILY = """
CREATE MATERIALIZED VIEW sales_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 day', bucket) AS bucket,
       tenant_id, store_id, product_id, channel,
       sum(revenue)      AS revenue,
       sum(units)        AS units,
       sum(transactions) AS transactions
FROM sales_hourly
GROUP BY time_bucket('1 day', bucket), tenant_id, store_id, product_id, channel
WITH NO DATA
"""


def upgrade() -> None:
    # CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous) cannot run in a transaction.
    with op.get_context().autocommit_block():
        op.execute(HOURLY)
        op.execute(DAILY)

    op.execute(
        "SELECT add_continuous_aggregate_policy('sales_hourly', "
        "start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute', "
        "schedule_interval => INTERVAL '1 minute')"
    )
    op.execute(
        "SELECT add_continuous_aggregate_policy('sales_daily', "
        "start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 minute', "
        "schedule_interval => INTERVAL '5 minutes')"
    )
    op.execute(
        "CREATE INDEX ix_sales_hourly_tenant_bucket ON sales_hourly (tenant_id, bucket DESC)"
    )
    op.execute("CREATE INDEX ix_sales_daily_tenant_bucket ON sales_daily (tenant_id, bucket DESC)")

    # Aggregates are cheap and answer most questions: keep hourly 2 years, daily forever.
    op.execute("ALTER MATERIALIZED VIEW sales_hourly SET (timescaledb.compress = true)")
    op.execute("SELECT add_compression_policy('sales_hourly', INTERVAL '30 days')")
    op.execute("SELECT add_retention_policy('sales_hourly', INTERVAL '2 years')")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP MATERIALIZED VIEW IF EXISTS sales_daily")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS sales_hourly")
