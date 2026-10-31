"""Reference tables: tenants, stores, products, users, export_jobs

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _uuid_pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _created_at() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        _created_at(),
    )
    op.create_table(
        "stores",
        _uuid_pk(),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("api_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
    )
    op.create_index("ix_stores_tenant_id", "stores", ["tenant_id"])
    op.create_table(
        "products",
        _uuid_pk(),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("list_price", sa.Numeric(12, 2), nullable=False),
        _created_at(),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_id"),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        _created_at(),
        sa.CheckConstraint("role in ('viewer','analyst','admin')", name="ck_users_role_valid"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_table(
        "export_jobs",
        _uuid_pk(),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("file_key", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("row_count", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint("format in ('csv','xlsx')", name="ck_export_jobs_format_valid"),
        sa.CheckConstraint(
            "status in ('queued','running','succeeded','failed','expired')",
            name="ck_export_jobs_status_valid",
        ),
    )
    op.create_index("ix_export_jobs_tenant_id", "export_jobs", ["tenant_id"])
    op.create_index("ix_export_jobs_expires_at", "export_jobs", ["expires_at"])


def downgrade() -> None:
    op.drop_table("export_jobs")
    op.drop_table("users")
    op.drop_table("products")
    op.drop_table("stores")
    op.drop_table("tenants")
