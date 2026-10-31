"""Schema validation and line-item explosion for consumed messages (plan sections 5 and 7)."""

import contextlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.schemas.events import SUPPORTED_VERSIONS, SaleEvent


class EventRejectedError(Exception):
    """The message can never succeed; it goes to the DLQ with `reason` in its headers."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ParsedMessage:
    event: SaleEvent
    tenant_id: uuid.UUID
    request_id: str | None
    rows: list[dict[str, Any]]


def headers_to_dict(headers: Iterable[tuple[str, bytes | None]] | None) -> dict[str, str]:
    return {k: (v.decode("utf-8", "replace") if v is not None else "") for k, v in headers or []}


def explode(
    event: SaleEvent, tenant_id: uuid.UUID, request_id: str | None, received_at: datetime
) -> list[dict[str, Any]]:
    """One row per line item. `txn` is 1 on the first row only so sum(txn) counts transactions."""
    return [
        {
            "event_time": event.event_time,
            "event_id": event.event_id,
            "product_id": item.product_id,
            "received_at": received_at,
            "tenant_id": tenant_id,
            "store_id": event.store_id,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "amount": item.amount,  # recomputed here, never trusted from the producer
            "currency": event.currency,
            "channel": event.channel,
            "txn": 1 if index == 0 else 0,
            "request_id": request_id,
        }
        for index, item in enumerate(event.items)
    ]


def parse_message(value: bytes | None, headers: dict[str, str]) -> ParsedMessage:
    if not value:
        raise EventRejectedError("empty_message")
    try:
        event = SaleEvent.model_validate_json(value)
    except ValidationError as exc:
        # An unknown version is reported as such even if other fields also changed.
        reason = "invalid_schema"
        try:
            import json

            raw = json.loads(value)
            if isinstance(raw, dict) and raw.get("event_version") not in SUPPORTED_VERSIONS:
                reason = "unsupported_version"
        except ValueError:
            reason = "invalid_json"
        first = exc.errors()[0]
        raise EventRejectedError(
            reason, f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
        ) from exc

    if event.event_version not in SUPPORTED_VERSIONS:
        raise EventRejectedError("unsupported_version", str(event.event_version))
    try:
        tenant_id = uuid.UUID(headers["tenant-id"])
    except (KeyError, ValueError):
        raise EventRejectedError("missing_tenant_header") from None

    received_at = datetime.now(UTC)
    if produced := headers.get("produced-at"):
        with contextlib.suppress(ValueError):
            received_at = datetime.fromisoformat(produced).astimezone(UTC)
    request_id = headers.get("request-id")
    return ParsedMessage(
        event, tenant_id, request_id, explode(event, tenant_id, request_id, received_at)
    )
