"""sales_events hypertable, indexes, compression and retention for raw events

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_events",
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("txn", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("request_id", sa.Text()),
        sa.PrimaryKeyConstraint("event_time", "event_id", "product_id", name="pk_sales_events"),
        sa.CheckConstraint("quantity > 0", name="ck_sales_events_quantity_positive"),
    )
    op.execute(
        "SELECT create_hypertable('sales_events', 'event_time', "
        "chunk_time_interval => INTERVAL '1 day')"
    )
    op.execute(
        "CREATE INDEX ix_sales_events_tenant_store_time "
        "ON sales_events (tenant_id, store_id, event_time DESC)"
    )
    op.execute(
        "CREATE INDEX ix_sales_events_tenant_product_time "
        "ON sales_events (tenant_id, product_id, event_time DESC)"
    )

    # Raw events are expensive to keep: compress after 7 days, drop after N days (default 90).
    op.execute(
        "ALTER TABLE sales_events SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'tenant_id, store_id', "
        "timescaledb.compress_orderby = 'event_time DESC')"
    )
    op.execute("SELECT add_compression_policy('sales_events', INTERVAL '7 days')")
    days = int(get_settings().raw_retention_days)
    op.execute(f"SELECT add_retention_policy('sales_events', INTERVAL '{days} days')")


def downgrade() -> None:
    op.drop_table("sales_events")
