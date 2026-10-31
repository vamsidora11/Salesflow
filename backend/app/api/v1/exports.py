import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import AnalystRole, get_csv_stream_service, get_export_service
from app.api.responses import AUTHED, INVALID_RANGE, NOT_FOUND
from app.schemas.common import ErrorResponse
from app.schemas.exports import DownloadResponse, ExportCreate, ExportFilters, ExportJobResponse
from app.services.exports import CsvStreamService, ExportService

router = APIRouter(prefix="/exports", tags=["exports"], responses=AUTHED)

Jobs = Annotated[ExportService, Depends(get_export_service)]


@router.get(
    "/csv",
    summary="Stream a CSV export",
    description="Streams rows with a server-side cursor. Up to 1,000,000 rows; above that, "
    "use `POST /exports`.",
    response_class=StreamingResponse,
    responses={
        **INVALID_RANGE,
        200: {"content": {"text/csv": {}}, "description": "CSV file."},
        413: {"model": ErrorResponse, "description": "Too many rows for a synchronous export."},
    },
)
async def export_csv(
    user: AnalystRole,
    streamer: Annotated[CsvStreamService, Depends(get_csv_stream_service)],
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    granularity: Literal["hour", "day"] = "day",
    store_id: uuid.UUID | None = None,
    region: Annotated[str | None, Query(max_length=100)] = None,
    channel: Literal["pos", "online", "mobile"] | None = None,
) -> StreamingResponse:
    filters = ExportFilters(
        date_from=from_,
        date_to=to,
        granularity=granularity,
        store_id=store_id,
        region=region,
        channel=channel,
    )
    aggregate_filters, _ = await streamer.prepare(user.tenant_id, filters)
    filename = f"salesflow-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.csv"
    return StreamingResponse(
        streamer.stream(aggregate_filters, filters),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "",
    summary="Request an asynchronous export",
    description="Queues a Celery job that writes XLSX (or a large CSV) to object storage.",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExportJobResponse,
    responses=INVALID_RANGE,
)
async def create_export(user: AnalystRole, service: Jobs, body: ExportCreate) -> ExportJobResponse:
    job = await service.create(user.tenant_id, user.id, body)
    return ExportJobResponse.model_validate(job, from_attributes=True)


@router.get(
    "/{job_id}",
    summary="Export job status",
    response_model=ExportJobResponse,
    responses=NOT_FOUND,
)
async def get_export(user: AnalystRole, service: Jobs, job_id: uuid.UUID) -> ExportJobResponse:
    job = await service.get(user.tenant_id, job_id)
    return ExportJobResponse.model_validate(job, from_attributes=True)


@router.get(
    "/{job_id}/download",
    summary="Get a presigned download URL",
    description="The URL is valid for 15 minutes.",
    response_model=DownloadResponse,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "Export is not finished yet."},
        410: {"model": ErrorResponse, "description": "Export file has expired."},
    },
)
async def download_export(user: AnalystRole, service: Jobs, job_id: uuid.UUID) -> DownloadResponse:
    return await service.download(user.tenant_id, job_id, datetime.now(UTC))
