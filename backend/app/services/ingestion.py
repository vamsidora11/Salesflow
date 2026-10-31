import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.core.metrics import INGEST_EVENTS
from app.repositories.stores import StoreIdentity
from app.schemas.events import SUPPORTED_VERSIONS, IngestResponse, RejectedEvent, SaleEvent
from app.streaming.producer import EventPublisher, OutboundMessage

logger = logging.getLogger(__name__)

SCHEMA_VERSION_HEADER = "schema-version"


def _short_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(p) for p in first["loc"])
    return f"{location}: {first['msg']}" if location else str(first["msg"])


class IngestionService:
    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def ingest(
        self, identity: StoreIdentity, raw_events: list[dict[str, Any]], request_id: str
    ) -> IngestResponse:
        """Validate, then write valid events to Kafka. Raises KafkaUnavailableError on failure."""
        valid: list[SaleEvent] = []
        rejected: list[RejectedEvent] = []

        for index, raw in enumerate(raw_events):
            event_id = str(raw.get("event_id")) if isinstance(raw, dict) else None
            try:
                event = SaleEvent.model_validate(raw)
            except ValidationError as exc:
                rejected.append(
                    RejectedEvent(index=index, event_id=event_id, error=_short_error(exc))
                )
                continue
            if event.event_version not in SUPPORTED_VERSIONS:
                rejected.append(
                    RejectedEvent(
                        index=index,
                        event_id=event_id,
                        error=f"unsupported event_version {event.event_version}",
                    )
                )
            elif event.store_id != identity.store_id:
                rejected.append(
                    RejectedEvent(
                        index=index,
                        event_id=event_id,
                        error="store_id does not match the API key's store",
                    )
                )
            else:
                valid.append(event)

        if valid:
            produced_at = datetime.now(UTC).isoformat()
            messages = [
                OutboundMessage(
                    key=str(event.store_id),
                    value=event.model_dump_json().encode(),
                    headers={
                        "tenant-id": str(identity.tenant_id),
                        SCHEMA_VERSION_HEADER: str(event.event_version),
                        "produced-at": produced_at,
                        "request-id": request_id,
                    },
                )
                for event in valid
            ]
            await self._publisher.publish(messages)

        INGEST_EVENTS.labels(result="accepted").inc(len(valid))
        INGEST_EVENTS.labels(result="rejected").inc(len(rejected))
        logger.info(
            "ingest_batch",
            extra={
                "accepted": len(valid),
                "rejected": len(rejected),
                "store_id": str(identity.store_id),
            },
        )
        return IngestResponse(accepted=len(valid), rejected=len(rejected), rejected_events=rejected)
