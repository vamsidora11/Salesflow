import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from celery.schedules import crontab

from app.db.models import ExportJob
from app.repositories.reconciliation import find_mismatches
from app.services import jobs as job_logic
from app.tasks.celery_app import build_beat_schedule, celery_app

STORE = uuid.uuid4()
DAY = datetime(2026, 10, 29, tzinfo=UTC)


class FakeFactory:
    @asynccontextmanager
    async def __call__(self) -> Any:
        yield object()


def test_find_mismatches_detects_revenue_and_unit_differences_and_missing_rows() -> None:
    other = uuid.uuid4()
    raw = {(STORE, DAY): (Decimal("100.00"), 10), (other, DAY): (Decimal("5.00"), 1)}
    agg = {(STORE, DAY): (Decimal("100.00"), 10)}  # `other` is missing from the aggregate
    result = find_mismatches(raw, agg)
    assert [m.store_id for m in result] == [other]
    assert result[0].raw_revenue == Decimal("5.00") and result[0].aggregate_revenue == 0

    drift = find_mismatches(
        {(STORE, DAY): (Decimal("100.00"), 10)}, {(STORE, DAY): (Decimal("99.00"), 10)}
    )
    assert len(drift) == 1
    assert find_mismatches(raw, raw) == []


async def test_reconcile_logs_an_error_for_a_deliberate_discrepancy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FakeRepo:
        def __init__(self, session: object) -> None:
            pass

        async def raw_daily_totals(self, start: datetime, end: datetime) -> dict[Any, Any]:
            assert end - start == timedelta(days=2) and end.hour == 0  # closed days only
            return {(STORE, DAY): (Decimal("120.00"), 12)}

        async def aggregate_daily_totals(self, start: datetime, end: datetime) -> dict[Any, Any]:
            return {(STORE, DAY): (Decimal("100.00"), 10)}

    monkeypatch.setattr(job_logic, "ReconciliationRepository", FakeRepo)
    with caplog.at_level(logging.ERROR):
        mismatches = await job_logic.reconcile_daily_totals(
            FakeFactory(), now=DAY + timedelta(days=1, hours=1)
        )  # type: ignore[arg-type]
    assert len(mismatches) == 1
    record = next(r for r in caplog.records if r.getMessage() == "reconciliation_mismatch")
    assert record.levelno == logging.ERROR
    assert record.raw_revenue == "120.00" and record.aggregate_revenue == "100.00"  # type: ignore[attr-defined]


async def test_reconcile_is_quiet_when_totals_match(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Same:
        def __init__(self, session: object) -> None:
            pass

        async def raw_daily_totals(self, *_: Any) -> dict[Any, Any]:
            return {(STORE, DAY): (Decimal("1.00"), 1)}

        aggregate_daily_totals = raw_daily_totals

    monkeypatch.setattr(job_logic, "ReconciliationRepository", Same)
    with caplog.at_level(logging.ERROR):
        assert await job_logic.reconcile_daily_totals(FakeFactory()) == []  # type: ignore[arg-type]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- cleanup_expired_exports --------------------------------------------------------------------


class FakeJobsRepo:
    jobs: list[ExportJob] = []
    expired: list[uuid.UUID] = []

    def __init__(self, session: object) -> None:
        pass

    async def list_expired(self, now: datetime) -> list[ExportJob]:
        return [
            j for j in self.jobs if j.expires_at and j.expires_at < now and j.status == "succeeded"
        ]

    async def mark_expired(self, job_id: uuid.UUID) -> None:
        self.expired.append(job_id)


class FakeObjectStore:
    def __init__(self, fail_keys: set[str] | None = None) -> None:
        self.deleted: list[str] = []
        self.fail_keys = fail_keys or set()

    def delete(self, key: str) -> None:
        if key in self.fail_keys:
            raise RuntimeError("s3 down")
        self.deleted.append(key)


def export_job(key: str, expires: datetime) -> ExportJob:
    return ExportJob(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        requested_by=uuid.uuid4(),
        format="csv",
        filters={},
        status="succeeded",
        file_key=key,
        expires_at=expires,
    )


async def test_cleanup_deletes_expired_files_and_marks_jobs_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    old, fresh = (
        export_job("old.csv", now - timedelta(hours=1)),
        export_job("new.csv", now + timedelta(days=1)),
    )
    FakeJobsRepo.jobs, FakeJobsRepo.expired = [old, fresh], []
    monkeypatch.setattr(job_logic, "ExportJobRepository", FakeJobsRepo)
    storage = FakeObjectStore()
    cleaned = await job_logic.cleanup_expired_exports(FakeFactory(), storage, now)  # type: ignore[arg-type]
    assert cleaned == 1
    assert storage.deleted == ["old.csv"]
    assert FakeJobsRepo.expired == [old.id]


async def test_cleanup_keeps_the_job_for_retry_when_storage_deletion_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    FakeJobsRepo.jobs, FakeJobsRepo.expired = (
        [export_job("stuck.csv", now - timedelta(hours=1))],
        [],
    )
    monkeypatch.setattr(job_logic, "ExportJobRepository", FakeJobsRepo)
    cleaned = await job_logic.cleanup_expired_exports(
        FakeFactory(), FakeObjectStore({"stuck.csv"}), now
    )  # type: ignore[arg-type]
    assert cleaned == 0 and FakeJobsRepo.expired == []


# --- Celery Beat -------------------------------------------------------------------------------


def test_beat_schedule_matches_the_plan() -> None:
    schedule = build_beat_schedule()
    assert set(schedule) == {
        "reconcile_daily_totals",
        "generate_daily_report",
        "cleanup_expired_exports",
        "refresh_dead_letter_summary",
    }
    reconcile = schedule["reconcile_daily_totals"]["schedule"]
    assert isinstance(reconcile, crontab) and reconcile.hour == {1} and reconcile.minute == {0}
    report = schedule["generate_daily_report"]["schedule"]
    assert report.hour == {6} and report.minute == {0}
    assert schedule["cleanup_expired_exports"]["schedule"].minute == {0}  # hourly
    assert schedule["refresh_dead_letter_summary"]["schedule"].minute == {0, 15, 30, 45}


def test_every_scheduled_task_is_registered_with_the_worker() -> None:
    import app.tasks.jobs  # noqa: F401  (registers tasks)

    for entry in build_beat_schedule().values():
        assert entry["task"] in celery_app.tasks
    assert "app.tasks.jobs.run_export" in celery_app.tasks


def test_short_test_interval_makes_every_job_due_quickly() -> None:
    from celery.schedules import maybe_schedule

    schedule = build_beat_schedule(test_interval_seconds=1)
    assert len(schedule) == 4
    long_ago = celery_app.now() - timedelta(seconds=5)
    just_now = celery_app.now()
    for entry in schedule.values():
        interval = maybe_schedule(entry["schedule"], app=celery_app)
        assert interval.is_due(long_ago)[0] is True  # fires on the next beat tick
        assert interval.is_due(just_now)[0] is False  # and not more often than the interval
