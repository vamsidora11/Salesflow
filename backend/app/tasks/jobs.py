"""Celery tasks. Each runs its async implementation in a fresh event loop."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import SessionFactoryT, make_engine, make_session_factory
from app.services import jobs as job_logic
from app.storage.s3 import ObjectStore, S3ObjectStore
from app.streaming.dlq import summarize_dlq_topic
from app.tasks.celery_app import celery_app

configure_logging(get_settings().log_level)


async def _run(fn: Callable[[SessionFactoryT, ObjectStore, Settings], Awaitable[Any]]) -> Any:
    settings = get_settings()
    engine = make_engine(settings.database_url, null_pool=True)  # new loop per task
    try:
        return await fn(make_session_factory(engine), S3ObjectStore(settings), settings)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.jobs.run_export")
def run_export(job_id: str) -> None:
    asyncio.run(_run(lambda f, s, cfg: job_logic.run_export_job(uuid.UUID(job_id), f, s, cfg)))


@celery_app.task(name="app.tasks.jobs.reconcile_daily_totals")
def reconcile_daily_totals() -> int:
    mismatches = asyncio.run(_run(lambda f, s, cfg: job_logic.reconcile_daily_totals(f)))
    return len(mismatches)


@celery_app.task(name="app.tasks.jobs.generate_daily_report")
def generate_daily_report() -> int:
    keys = asyncio.run(_run(lambda f, s, cfg: job_logic.generate_daily_report(f, s)))
    return len(keys)


@celery_app.task(name="app.tasks.jobs.cleanup_expired_exports")
def cleanup_expired_exports() -> int:
    return int(asyncio.run(_run(lambda f, s, cfg: job_logic.cleanup_expired_exports(f, s))))


@celery_app.task(name="app.tasks.jobs.refresh_dead_letter_summary")
def refresh_dead_letter_summary() -> dict[str, int]:
    return summarize_dlq_topic(get_settings())
