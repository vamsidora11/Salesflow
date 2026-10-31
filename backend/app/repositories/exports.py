import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExportJob
from app.db.models.base import utcnow
from app.repositories.filters import AGGREGATE_TABLES, AggregateFilters, build_where


@dataclass(frozen=True)
class ExportRow:
    bucket: datetime
    store: str
    region: str
    sku: str
    product: str
    channel: str
    revenue: Decimal
    units: int
    transactions: int


class ExportJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, requested_by: uuid.UUID, fmt: str, filters: dict[str, Any]
    ) -> ExportJob:
        job = ExportJob(tenant_id=tenant_id, requested_by=requested_by, format=fmt, filters=filters)
        self._session.add(job)
        await self._session.commit()
        return job

    async def get_for_tenant(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> ExportJob | None:
        """Tenant-scoped: another tenant's job id is indistinguishable from a missing one."""
        return await self._session.scalar(
            select(ExportJob).where(ExportJob.id == job_id, ExportJob.tenant_id == tenant_id)
        )

    async def get(self, job_id: uuid.UUID) -> ExportJob | None:
        """Unscoped; for the worker, which only knows the job id it was handed."""
        return await self._session.scalar(select(ExportJob).where(ExportJob.id == job_id))

    async def _update(self, job_id: uuid.UUID, **values: Any) -> None:
        await self._session.execute(
            update(ExportJob).where(ExportJob.id == job_id).values(**values)
        )
        await self._session.commit()

    async def mark_running(self, job_id: uuid.UUID) -> None:
        await self._update(job_id, status="running", started_at=utcnow(), error=None)

    async def set_progress(self, job_id: uuid.UUID, row_count: int) -> None:
        await self._update(job_id, row_count=row_count)

    async def mark_succeeded(
        self, job_id: uuid.UUID, file_key: str, row_count: int, expires_at: datetime
    ) -> None:
        await self._update(
            job_id,
            status="succeeded",
            file_key=file_key,
            row_count=row_count,
            completed_at=utcnow(),
            expires_at=expires_at,
        )

    async def mark_failed(self, job_id: uuid.UUID, error: str) -> None:
        await self._update(job_id, status="failed", error=error[:500], completed_at=utcnow())

    async def list_expired(self, now: datetime) -> list[ExportJob]:
        return list(
            (
                await self._session.scalars(
                    select(ExportJob).where(
                        ExportJob.expires_at < now, ExportJob.status == "succeeded"
                    )
                )
            ).all()
        )

    async def mark_expired(self, job_id: uuid.UUID) -> None:
        await self._update(job_id, status="expired", file_key=None)


class ExportDataRepository:
    """Row source for exports: continuous aggregates joined to store/product names."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_rows(self, f: AggregateFilters, granularity: Literal["hour", "day"]) -> int:
        where, params = build_where("t", f)
        table = AGGREGATE_TABLES[granularity]
        return int(
            await self._session.scalar(
                text(f"SELECT count(*) FROM {table} t WHERE {where}"), params
            )
            or 0
        )

    async def stream_rows(
        self, f: AggregateFilters, granularity: Literal["hour", "day"], chunk_rows: int
    ) -> AsyncIterator[ExportRow]:
        """Server-side cursor: memory stays flat regardless of result size."""
        where, params = build_where("t", f)
        table = AGGREGATE_TABLES[granularity]
        stmt = text(
            "SELECT t.bucket AS bucket, coalesce(s.name, t.store_id::text) AS store,"
            " coalesce(s.region, '') AS region,"
            " coalesce(p.sku, t.product_id::text) AS sku, coalesce(p.name, '') AS product,"
            " t.channel AS channel, t.revenue AS revenue, t.units AS units,"
            " t.transactions AS transactions"
            f" FROM {table} t"
            " LEFT JOIN stores s ON s.id = t.store_id AND s.tenant_id = t.tenant_id"
            " LEFT JOIN products p ON p.id = t.product_id AND p.tenant_id = t.tenant_id"
            f" WHERE {where} ORDER BY t.bucket, store, sku, t.channel"
        ).execution_options(yield_per=chunk_rows)
        result = await self._session.stream(stmt, params)
        async for r in result:
            yield ExportRow(
                bucket=r.bucket,
                store=r.store,
                region=r.region,
                sku=r.sku,
                product=r.product,
                channel=r.channel,
                revenue=Decimal(r.revenue),
                units=int(r.units),
                transactions=int(r.transactions),
            )
