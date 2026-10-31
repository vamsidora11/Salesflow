import uuid
from datetime import timedelta
from typing import Any

from celery import Celery
from celery.schedules import crontab

from app.core.config import Settings, get_settings


def build_beat_schedule(test_interval_seconds: float | None = None) -> dict[str, dict[str, Any]]:
    """Plan section 8. With `test_interval_seconds`, every job fires on that short interval."""
    cron: dict[str, Any] = {
        "reconcile_daily_totals": (
            "app.tasks.jobs.reconcile_daily_totals",
            crontab(hour=1, minute=0),
        ),
        "generate_daily_report": (
            "app.tasks.jobs.generate_daily_report",
            crontab(hour=6, minute=0),
        ),
        "cleanup_expired_exports": ("app.tasks.jobs.cleanup_expired_exports", crontab(minute=0)),
        "refresh_dead_letter_summary": (
            "app.tasks.jobs.refresh_dead_letter_summary",
            crontab(minute="*/15"),
        ),
    }
    schedule: dict[str, dict[str, Any]] = {}
    for name, (task, when) in cron.items():
        if test_interval_seconds is not None:
            when = timedelta(seconds=test_interval_seconds)
        schedule[name] = {"task": task, "schedule": when}
    return schedule


def make_celery(settings: Settings) -> Celery:
    app = Celery("salesflow", broker=settings.redis_url, include=["app.tasks.jobs"])
    app.conf.update(
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        beat_schedule=build_beat_schedule(settings.beat_test_interval_seconds),
    )
    return app


celery_app = make_celery(get_settings())


class CeleryExportQueue:
    """Adapter used by ExportService to enqueue export jobs."""

    def enqueue(self, job_id: uuid.UUID) -> None:
        celery_app.send_task("app.tasks.jobs.run_export", args=[str(job_id)])
