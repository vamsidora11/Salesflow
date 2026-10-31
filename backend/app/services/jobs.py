"""Business logic of the batch layer. Celery tasks (app.tasks.jobs) are thin wrappers."""

import asyncio
import json
import logging
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.metrics import RECONCILIATION_MISMATCHES
from app.repositories.analytics import AnalyticsRepository
from app.repositories.exports import ExportDataRepository, ExportJobRepository, ExportRow
from app.repositories.filters import AggregateFilters
from app.repositories.reconciliation import Mismatch, ReconciliationRepository, find_mismatches
from app.repositories.tenants import TenantRepository
from app.schemas.exports import ExportFilters
from app.services.export_files import XlsxExportWriter, csv_chunks
from app.services.exports import to_aggregate_filters
from app.storage.s3 import ObjectStore

logger = logging.getLogger(__name__)
SessionFactory = async_sessionmaker[AsyncSession]

CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def day_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


# --- reconcile_daily_totals ------------------------------------------------------------------


async def reconcile_daily_totals(
    factory: SessionFactory, now: datetime | None = None, lookback_days: int = 2
) -> list[Mismatch]:
    """Compare aggregate totals with raw totals for the last closed days. Logs every mismatch."""
    today = day_start(now or datetime.now(UTC))
    start = today - timedelta(days=lookback_days)
    async with factory() as session:
        repo = ReconciliationRepository(session)
        raw = await repo.raw_daily_totals(start, today)
        aggregate = await repo.aggregate_daily_totals(start, today)
    mismatches = find_mismatches(raw, aggregate)
    for m in mismatches:
        logger.error(
            "reconciliation_mismatch",
            extra={
                "store_id": str(m.store_id),
                "day": m.day.isoformat(),
                "raw_revenue": str(m.raw_revenue),
                "aggregate_revenue": str(m.aggregate_revenue),
                "raw_units": m.raw_units,
                "aggregate_units": m.aggregate_units,
            },
        )
    RECONCILIATION_MISMATCHES.set(len(mismatches))
    logger.info("reconciliation_done", extra={"mismatches": len(mismatches)})
    return mismatches


# --- cleanup_expired_exports -----------------------------------------------------------------


async def cleanup_expired_exports(
    factory: SessionFactory, storage: ObjectStore, now: datetime | None = None
) -> int:
    """Delete files whose expires_at passed and mark the jobs expired."""
    now = now or datetime.now(UTC)
    cleaned = 0
    async with factory() as session:
        jobs = ExportJobRepository(session)
        for job in await jobs.list_expired(now):
            if job.file_key:
                try:
                    await asyncio.to_thread(storage.delete, job.file_key)
                except Exception:
                    logger.warning("export_delete_failed", extra={"job_id": str(job.id)})
                    continue  # leave it succeeded so the next run retries
            await jobs.mark_expired(job.id)
            cleaned += 1
    logger.info("cleanup_expired_exports_done", extra={"cleaned": cleaned})
    return cleaned


# --- run_export ------------------------------------------------------------------------------


class _CountingRows:
    """Wraps a row stream and reports progress every `every` rows."""

    def __init__(
        self,
        rows: AsyncIterator[ExportRow],
        every: int,
        on_progress: Callable[[int], Awaitable[None]],
    ) -> None:
        self._rows = rows
        self._every = every
        self._on_progress = on_progress
        self.count = 0

    def __aiter__(self) -> AsyncIterator[ExportRow]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[ExportRow]:
        async for row in self._rows:
            self.count += 1
            yield row
            if self.count % self._every == 0:
                await self._on_progress(self.count)


async def _summary_for(
    session: AsyncSession, f: AggregateFilters, filters: ExportFilters
) -> dict[str, Any]:
    totals = await AnalyticsRepository(session).summary(f)
    aov = (
        (totals.revenue / totals.transactions).quantize(Decimal("0.01"))
        if totals.transactions
        else 0
    )
    return {
        "Revenue": totals.revenue,
        "Units": totals.units,
        "Transactions": totals.transactions,
        "Average order value": aov,
        "From (UTC)": f.start.isoformat(),
        "To (UTC, exclusive)": f.end.isoformat(),
        "Granularity": filters.granularity,
        "Store": str(filters.store_id) if filters.store_id else "all",
        "Region": filters.region or "all",
        "Channel": filters.channel or "all",
        "Generated at (UTC)": datetime.now(UTC).isoformat(),
    }


async def run_export_job(
    job_id: uuid.UUID, factory: SessionFactory, storage: ObjectStore, settings: Settings
) -> None:
    # Separate sessions: the streaming cursor must not share a transaction with status commits.
    async with factory() as job_session, factory() as data_session:
        jobs = ExportJobRepository(job_session)
        job = await jobs.get(job_id)
        if job is None or job.status in ("succeeded", "expired"):
            return
        await jobs.mark_running(job_id)
        try:
            filters = ExportFilters.model_validate(job.filters)
            f = to_aggregate_filters(job.tenant_id, filters, settings)
            fmt = job.format
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"{job_id}.{fmt}"

                async def progress(n: int, _jobs: ExportJobRepository = jobs) -> None:
                    await _jobs.set_progress(job_id, n)

                rows = _CountingRows(
                    ExportDataRepository(data_session).stream_rows(
                        f, filters.granularity, settings.export_chunk_rows
                    ),
                    settings.export_chunk_rows,
                    progress,
                )
                if fmt == "xlsx":
                    summary = await _summary_for(data_session, f, filters)
                    writer = XlsxExportWriter(
                        path, filters.granularity, summary, settings.excel_max_rows_per_sheet
                    )
                    async for row in rows:
                        writer.write(row)
                    writer.close()
                else:
                    with path.open("w", encoding="utf-8", newline="") as handle:
                        async for chunk in csv_chunks(rows, filters.granularity):
                            handle.write(chunk)
                key = f"exports/{job.tenant_id}/{job_id}.{fmt}"
                await asyncio.to_thread(storage.ensure_bucket)
                await asyncio.to_thread(storage.upload_file, path, key, CONTENT_TYPES[fmt])
            expires = datetime.now(UTC) + timedelta(days=settings.export_ttl_days)
            await jobs.mark_succeeded(job_id, key, rows.count, expires)
            logger.info("export_succeeded", extra={"job_id": str(job_id), "rows": rows.count})
        except Exception as exc:
            logger.exception("export_failed", extra={"job_id": str(job_id)})
            await job_session.rollback()
            await jobs.mark_failed(job_id, f"{type(exc).__name__}: {exc}")


# --- generate_daily_report -------------------------------------------------------------------


async def generate_daily_report(
    factory: SessionFactory, storage: ObjectStore, now: datetime | None = None
) -> list[str]:
    """Per-tenant JSON summary of the previous UTC day, stored in object storage."""
    today = day_start(now or datetime.now(UTC))
    yesterday = today - timedelta(days=1)
    keys: list[str] = []
    await asyncio.to_thread(storage.ensure_bucket)
    async with factory() as session:
        tenants = await TenantRepository(session).list_all()
        analytics = AnalyticsRepository(session)
        for tenant_id, name, currency in tenants:
            f = AggregateFilters(tenant_id, yesterday, today)
            summary = await analytics.summary(f)
            report = {
                "tenant": name,
                "date": yesterday.date().isoformat(),
                "currency": currency,
                "revenue": str(summary.revenue),
                "units": summary.units,
                "transactions": summary.transactions,
                "top_products": [
                    {"sku": p.sku, "name": p.name, "revenue": str(p.revenue), "units": p.units}
                    for p in await analytics.top_products(f, 10)
                ],
                "by_region": [
                    {"region": r.label, "revenue": str(r.revenue)}
                    for r in await analytics.by_region(f)
                ],
            }
            key = f"reports/{tenant_id}/{yesterday.date().isoformat()}.json"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "report.json"
                path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                await asyncio.to_thread(storage.upload_file, path, key, "application/json")
            keys.append(key)
    logger.info("daily_report_done", extra={"reports": len(keys)})
    return keys
