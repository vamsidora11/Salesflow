import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import openpyxl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import ExportJob
from app.repositories.exports import ExportJobRepository
from app.repositories.sales_events import SalesEventRepository
from app.services import jobs as job_logic
from tests.integration.helpers import midnight, sale_rows, seed_world


class CapturingStore:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def ensure_bucket(self) -> None:
        return None

    def upload_file(self, path: Path, key: str, content_type: str) -> None:
        self.files[key] = path.read_bytes()

    def delete(self, key: str) -> None:
        self.deleted.append(key)


async def test_reconciliation_flags_a_deliberate_discrepancy(
    session_factory: async_sessionmaker[AsyncSession],
    pg: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = await seed_world(session_factory)
    when = midnight(1) + timedelta(hours=10)
    async with session_factory() as session, session.begin():
        await SalesEventRepository(session).insert_rows(sale_rows(world, when))
    await pg.execute("CALL refresh_continuous_aggregate('sales_hourly', NULL, NULL)")
    await pg.execute("CALL refresh_continuous_aggregate('sales_daily', NULL, NULL)")

    now = datetime.now(UTC)
    with caplog.at_level(logging.ERROR):
        clean = await job_logic.reconcile_daily_totals(session_factory, now)
    assert not [m for m in clean if m.store_id in world.stores.values()]

    # Deliberate discrepancy: an old-day event lands but the aggregate is not refreshed.
    async with session_factory() as session, session.begin():
        await SalesEventRepository(session).insert_rows(
            sale_rows(world, when + timedelta(minutes=5))
        )
    with caplog.at_level(logging.ERROR):
        mismatches = await job_logic.reconcile_daily_totals(session_factory, now)
    assert any(m.store_id == world.stores["Europe"] for m in mismatches)
    assert any(r.getMessage() == "reconciliation_mismatch" for r in caplog.records)


async def test_xlsx_export_job_end_to_end(
    session_factory: async_sessionmaker[AsyncSession], pg: asyncpg.Connection, tmp_path: Path
) -> None:
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        rows = []
        for day in (3, 2, 1):
            rows += sale_rows(world, midnight(day) + timedelta(hours=11))
        await SalesEventRepository(session).insert_rows(rows)
    await pg.execute("CALL refresh_continuous_aggregate('sales_hourly', NULL, NULL)")
    await pg.execute("CALL refresh_continuous_aggregate('sales_daily', NULL, NULL)")

    filters: dict[str, Any] = {
        "from": midnight(4).isoformat(),
        "to": midnight(0).isoformat(),
        "granularity": "day",
    }
    async with session_factory() as session:
        job = await ExportJobRepository(session).create(
            world.tenant_id, world.user_id, "xlsx", filters
        )

    store = CapturingStore()
    await job_logic.run_export_job(job.id, session_factory, store, Settings())  # type: ignore[arg-type]

    async with session_factory() as session:
        done = await session.scalar(select(ExportJob).where(ExportJob.id == job.id))
    assert done is not None and done.status == "succeeded", done and done.error
    assert done.row_count == 6  # 3 days x 2 products
    assert done.expires_at is not None and done.expires_at > datetime.now(UTC) + timedelta(days=6)

    out = tmp_path / "out.xlsx"
    out.write_bytes(store.files[done.file_key or ""])
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Summary", "Data"]
    assert wb["Data"].freeze_panes == "A2"
    assert wb["Data"].max_row == 7


async def test_failed_export_is_recorded(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = await seed_world(session_factory)
    bad = {"from": "2026-01-01T00:00:00Z", "to": "2020-01-01T00:00:00Z"}  # inverted
    async with session_factory() as session:
        job = await ExportJobRepository(session).create(world.tenant_id, world.user_id, "csv", bad)
    await job_logic.run_export_job(job.id, session_factory, CapturingStore(), Settings())  # type: ignore[arg-type]
    async with session_factory() as session:
        failed = await session.scalar(select(ExportJob).where(ExportJob.id == job.id))
    assert failed is not None and failed.status == "failed" and failed.error


async def test_cleanup_expires_old_exports(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await seed_world(session_factory)
    async with session_factory() as session:
        repo = ExportJobRepository(session)
        job = await repo.create(world.tenant_id, world.user_id, "csv", {})
        await repo.mark_succeeded(
            job.id, f"exports/{uuid.uuid4()}.csv", 1, datetime.now(UTC) - timedelta(hours=1)
        )
    store = CapturingStore()
    assert await job_logic.cleanup_expired_exports(session_factory, store) >= 1  # type: ignore[arg-type]
    assert len(store.deleted) >= 1
    async with session_factory() as session:
        refreshed = await session.scalar(select(ExportJob).where(ExportJob.id == job.id))
    assert refreshed is not None and refreshed.status == "expired" and refreshed.file_key is None
