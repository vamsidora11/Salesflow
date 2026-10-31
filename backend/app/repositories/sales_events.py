import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SalesEvent

# 13 columns per row; asyncpg allows 32767 parameters per statement.
INSERT_CHUNK_ROWS = 1000
RowKey = tuple[uuid.UUID, uuid.UUID]


class SalesEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_rows(self, rows: list[dict[str, Any]]) -> set[RowKey]:
        """Idempotent insert. Returns the (event_id, product_id) keys that were actually new."""
        inserted: set[RowKey] = set()
        for i in range(0, len(rows), INSERT_CHUNK_ROWS):
            stmt = (
                pg_insert(SalesEvent)
                .values(rows[i : i + INSERT_CHUNK_ROWS])
                .on_conflict_do_nothing(index_elements=["event_time", "event_id", "product_id"])
                .returning(SalesEvent.event_id, SalesEvent.product_id)
            )
            inserted.update((r.event_id, r.product_id) for r in await self._session.execute(stmt))
        return inserted

    async def bucket_totals(
        self, tenant_id: uuid.UUID, store_id: uuid.UUID, bucket: datetime
    ) -> tuple[Decimal, int, str]:
        """Current totals of one (store, hour) from raw events, plus the store's region."""
        row = (
            await self._session.execute(
                text(
                    "SELECT coalesce(sum(e.amount), 0) AS revenue,"
                    " coalesce(sum(e.quantity), 0) AS units,"
                    " coalesce((SELECT region FROM stores WHERE id = :store_id), '') AS region"
                    " FROM sales_events e"
                    " WHERE e.tenant_id = :tenant_id AND e.store_id = :store_id"
                    " AND e.event_time >= :start AND e.event_time < :end"
                ),
                {
                    "tenant_id": tenant_id,
                    "store_id": store_id,
                    "start": bucket,
                    "end": bucket + timedelta(hours=1),
                },
            )
        ).one()
        return Decimal(row.revenue), int(row.units), str(row.region)
