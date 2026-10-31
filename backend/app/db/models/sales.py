import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, DateTime, Integer, Numeric, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base

CHANNELS = ("pos", "online", "mobile")


class SalesEvent(Base):
    """One row per line item. Hypertable on event_time (created in the migration).

    The partitioning column must be part of every unique constraint, hence the composite key.
    """

    __tablename__ = "sales_events"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    # 1 on exactly one line item per event, so sum() counts transactions, not line items.
    txn: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    # Ties the row back to the ingestion request log line (plan section 16, phase 8).
    request_id: Mapped[str | None] = mapped_column(Text)
