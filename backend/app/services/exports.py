import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import ExportJob
from app.repositories.exports import ExportDataRepository, ExportJobRepository
from app.repositories.filters import AggregateFilters
from app.schemas.exports import DownloadResponse, ExportCreate, ExportFilters
from app.services.analytics import validate_range
from app.services.export_files import csv_chunks
from app.storage.s3 import ObjectStore


class ExportNotFoundError(Exception):
    pass


class ExportNotReadyError(Exception):
    """The job exists but has no downloadable file yet."""


class ExportExpiredError(Exception):
    pass


class ExportTooLargeError(Exception):
    def __init__(self, rows: int, limit: int) -> None:
        super().__init__(f"{rows} rows exceeds the synchronous limit of {limit}; use POST /exports")
        self.rows = rows
        self.limit = limit


class ExportQueue(Protocol):
    def enqueue(self, job_id: uuid.UUID) -> None: ...


def to_aggregate_filters(
    tenant_id: uuid.UUID, filters: ExportFilters, settings: Settings
) -> AggregateFilters:
    """Validate the range (cap per granularity) and bind the filters to the tenant."""
    start, end = validate_range(filters.date_from, filters.date_to, filters.granularity, settings)
    return AggregateFilters(
        tenant_id, start, end, filters.store_id, filters.region, filters.channel
    )


class ExportService:
    """Async exports: create job, enqueue, report status, hand out presigned URLs."""

    def __init__(
        self,
        jobs: ExportJobRepository,
        queue: ExportQueue,
        storage: ObjectStore,
        settings: Settings,
    ) -> None:
        self._jobs = jobs
        self._queue = queue
        self._storage = storage
        self._settings = settings

    async def create(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, request: ExportCreate
    ) -> ExportJob:
        to_aggregate_filters(tenant_id, request.filters, self._settings)  # validate before queueing
        stored: dict[str, Any] = request.filters.model_dump(mode="json", by_alias=True)
        job = await self._jobs.create(tenant_id, user_id, request.format, stored)
        self._queue.enqueue(job.id)
        return job

    async def get(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> ExportJob:
        job = await self._jobs.get_for_tenant(tenant_id, job_id)
        if job is None:
            raise ExportNotFoundError
        return job

    async def download(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID, now: datetime
    ) -> DownloadResponse:
        job = await self.get(tenant_id, job_id)
        if job.status == "expired" or (job.expires_at is not None and job.expires_at <= now):
            raise ExportExpiredError
        if job.status != "succeeded" or not job.file_key:
            raise ExportNotReadyError
        ttl = self._settings.export_download_ttl_seconds
        filename = f"salesflow-export-{job.id}.{job.format}"
        url = self._storage.presigned_get_url(job.file_key, ttl, filename)
        return DownloadResponse(url=url, expires_in=ttl)


class CsvStreamService:
    """Synchronous CSV: opens its own session because the response outlives the request scope."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._factory = session_factory
        self._settings = settings

    async def prepare(
        self, tenant_id: uuid.UUID, filters: ExportFilters
    ) -> tuple[AggregateFilters, int]:
        f = to_aggregate_filters(tenant_id, filters, self._settings)
        async with self._factory() as session:
            rows = await ExportDataRepository(session).count_rows(f, filters.granularity)
        if rows > self._settings.csv_sync_max_rows:
            raise ExportTooLargeError(rows, self._settings.csv_sync_max_rows)
        return f, rows

    async def stream(self, f: AggregateFilters, filters: ExportFilters) -> AsyncIterator[str]:
        async with self._factory() as session:
            rows = ExportDataRepository(session).stream_rows(
                f, filters.granularity, self._settings.export_chunk_rows
            )
            async for chunk in csv_chunks(rows, filters.granularity):
                yield chunk
