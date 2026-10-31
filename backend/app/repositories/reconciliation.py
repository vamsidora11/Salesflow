import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DayKey = tuple[uuid.UUID, datetime]
Totals = tuple[Decimal, int]


@dataclass(frozen=True)
class Mismatch:
    store_id: uuid.UUID
    day: datetime
    raw_revenue: Decimal
    aggregate_revenue: Decimal
    raw_units: int
    aggregate_units: int


def find_mismatches(raw: dict[DayKey, Totals], aggregate: dict[DayKey, Totals]) -> list[Mismatch]:
    """Compare per-(store, day) totals; a key missing on one side counts as zero."""
    zero: Totals = (Decimal(0), 0)
    result = []
    for key in sorted(raw.keys() | aggregate.keys(), key=lambda k: (str(k[0]), k[1])):
        raw_rev, raw_units = raw.get(key, zero)
        agg_rev, agg_units = aggregate.get(key, zero)
        if raw_rev != agg_rev or raw_units != agg_units:
            result.append(Mismatch(key[0], key[1], raw_rev, agg_rev, raw_units, agg_units))
    return result


class ReconciliationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def raw_daily_totals(self, start: datetime, end: datetime) -> dict[DayKey, Totals]:
        result = await self._session.execute(
            text(
                "SELECT store_id, time_bucket('1 day', event_time) AS day,"
                " sum(amount) AS revenue, sum(quantity) AS units"
                " FROM sales_events WHERE event_time >= :start AND event_time < :end"
                " GROUP BY store_id, day"
            ),
            {"start": start, "end": end},
        )
        return {(r.store_id, r.day): (Decimal(r.revenue), int(r.units)) for r in result}

    async def aggregate_daily_totals(self, start: datetime, end: datetime) -> dict[DayKey, Totals]:
        result = await self._session.execute(
            text(
                "SELECT store_id, bucket AS day, sum(revenue) AS revenue, sum(units) AS units"
                " FROM sales_daily WHERE bucket >= :start AND bucket < :end"
                " GROUP BY store_id, bucket"
            ),
            {"start": start, "end": end},
        )
        return {(r.store_id, r.day): (Decimal(r.revenue), int(r.units)) for r in result}
