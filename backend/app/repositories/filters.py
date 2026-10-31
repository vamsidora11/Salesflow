import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Table names and column names are chosen from these whitelists, never from user input.
AGGREGATE_TABLES = {"hour": "sales_hourly", "day": "sales_daily"}
METRIC_COLUMNS = {"revenue": "revenue", "units": "units"}


@dataclass(frozen=True)
class AggregateFilters:
    """Tenant-scoped filter for queries against the continuous aggregates.

    The time range is half-open: `start <= bucket < end`.
    """

    tenant_id: uuid.UUID
    start: datetime
    end: datetime
    store_id: uuid.UUID | None = None
    region: str | None = None
    channel: str | None = None


def build_where(alias: str, f: AggregateFilters) -> tuple[str, dict[str, Any]]:
    clauses = [
        f"{alias}.tenant_id = :tenant_id",
        f"{alias}.bucket >= :start",
        f"{alias}.bucket < :end",
    ]
    params: dict[str, Any] = {"tenant_id": f.tenant_id, "start": f.start, "end": f.end}
    if f.store_id is not None:
        clauses.append(f"{alias}.store_id = :store_id")
        params["store_id"] = f.store_id
    if f.region is not None:
        clauses.append(
            f"{alias}.store_id IN "
            "(SELECT id FROM stores WHERE tenant_id = :tenant_id AND region = :region)"
        )
        params["region"] = f.region
    if f.channel is not None:
        clauses.append(f"{alias}.channel = :channel")
        params["channel"] = f.channel
    return " AND ".join(clauses), params
