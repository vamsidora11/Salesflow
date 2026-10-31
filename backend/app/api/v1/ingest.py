import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.api.deps import IngestIdentity, SettingsDep, get_ingestion_service
from app.api.responses import UNAUTHORIZED
from app.core.logging import request_id_var
from app.schemas.common import ErrorResponse
from app.schemas.events import IngestRequest, IngestResponse
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/ingest", tags=["ingest"])

_REQUEST_BODY = {
    "required": True,
    "content": {"application/json": {"schema": IngestRequest.model_json_schema()}},
}


@router.post(
    "/events",
    summary="Ingest a batch of sale events",
    description=(
        "Validates each event and writes valid ones to Kafka (`acks=all`). **202 means the "
        "events are in the durable log, not yet in the database**; dashboards update shortly "
        "after. Invalid events are returned in `rejected_events`, not dropped silently. "
        "Maximum 500 events and 256 KB per request."
    ),
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResponse,
    openapi_extra={"requestBody": _REQUEST_BODY},
    responses={
        **UNAUTHORIZED,
        413: {"model": ErrorResponse, "description": "Body exceeds 256 KB."},
        422: {"model": ErrorResponse, "description": "Malformed envelope or too many events."},
        429: {"model": ErrorResponse, "description": "Per-key rate limit exceeded."},
        503: {
            "model": ErrorResponse,
            "description": "Kafka unavailable; retry after the `Retry-After` header.",
        },
    },
)
async def ingest_events(
    request: Request,
    identity: IngestIdentity,
    settings: SettingsDep,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> IngestResponse:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.ingest_max_body_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Body too large")
    body = await request.body()
    if len(body) > settings.ingest_max_body_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Body too large")
    try:
        envelope = IngestRequest.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        raise HTTPException(
            422,
            detail='Body must be JSON: {"events": [ ... at least one event ... ]}',
        ) from None
    if len(envelope.events) > settings.ingest_max_events:
        raise HTTPException(
            422,
            detail=f"At most {settings.ingest_max_events} events per request",
        )
    return await service.ingest(identity, envelope.events, request_id_var.get() or "unknown")
