import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.events import SaleEvent
from app.streaming.event_parser import (
    EventRejectedError,
    explode,
    headers_to_dict,
    parse_message,
)


def make_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "event_type": "sale.completed",
        "event_version": 1,
        "event_time": "2026-10-30T14:22:05Z",
        "store_id": str(uuid.uuid4()),
        "channel": "pos",
        "currency": "USD",
        "items": [
            {"product_id": str(uuid.uuid4()), "quantity": 2, "unit_price": "19.99"},
            {"product_id": str(uuid.uuid4()), "quantity": 1, "unit_price": "5.00"},
        ],
    }
    event.update(overrides)
    return event


def test_valid_event_computes_decimal_amounts() -> None:
    event = SaleEvent.model_validate(make_event())
    assert [i.amount for i in event.items] == [Decimal("39.98"), Decimal("5.00")]
    assert event.event_time.tzinfo is UTC


def test_money_is_serialised_as_a_string_never_a_number() -> None:
    event = SaleEvent.model_validate(make_event())
    payload = json.loads(event.model_dump_json())
    assert payload["items"][0]["unit_price"] == "19.99"


@pytest.mark.parametrize(
    "mutation",
    [
        {"event_time": "2026-10-30T14:22:05"},  # naive timestamp
        {"channel": "fax"},
        {"currency": "usd"},
        {"items": []},
        {"unexpected": 1},
        {"items": [{"product_id": str(uuid.uuid4()), "quantity": 0, "unit_price": "1.00"}]},
        {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1, "unit_price": 19.99}]},  # float
        {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1, "unit_price": "1.999"}]},
        {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1, "unit_price": "-1.00"}]},
    ],
)
def test_invalid_events_are_rejected(mutation: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SaleEvent.model_validate(make_event(**mutation))


def test_repeated_product_in_one_event_is_rejected() -> None:
    product = str(uuid.uuid4())
    items = [
        {"product_id": product, "quantity": 1, "unit_price": "1.00"},
        {"product_id": product, "quantity": 2, "unit_price": "1.00"},
    ]
    with pytest.raises(ValidationError, match="repeat a product_id"):
        SaleEvent.model_validate(make_event(items=items))


def test_explode_creates_one_row_per_item_with_single_transaction_flag() -> None:
    event = SaleEvent.model_validate(make_event())
    tenant = uuid.uuid4()
    rows = explode(event, tenant, "req-1", datetime.now(UTC))
    assert len(rows) == 2
    assert {r["event_id"] for r in rows} == {event.event_id}
    assert [r["txn"] for r in rows] == [1, 0]
    assert [r["amount"] for r in rows] == [Decimal("39.98"), Decimal("5.00")]
    assert all(r["tenant_id"] == tenant and r["request_id"] == "req-1" for r in rows)


def _headers(**extra: str) -> dict[str, str]:
    return {"tenant-id": str(uuid.uuid4()), "request-id": "req-9", **extra}


def test_parse_message_happy_path() -> None:
    parsed = parse_message(
        json.dumps(make_event()).encode(), _headers(**{"produced-at": "2026-10-30T14:22:06+00:00"})
    )
    assert len(parsed.rows) == 2
    assert parsed.request_id == "req-9"
    assert parsed.rows[0]["received_at"] == datetime(2026, 10, 30, 14, 22, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "headers", "reason"),
    [
        (None, _headers(), "empty_message"),
        (b"{not json", _headers(), "invalid_json"),
        (json.dumps(make_event(channel="fax")).encode(), _headers(), "invalid_schema"),
        (json.dumps(make_event(event_version=2)).encode(), _headers(), "unsupported_version"),
        (json.dumps(make_event()).encode(), {}, "missing_tenant_header"),
        (json.dumps(make_event()).encode(), {"tenant-id": "nope"}, "missing_tenant_header"),
    ],
)
def test_parse_message_rejects_with_reason(
    value: bytes | None, headers: dict[str, str], reason: str
) -> None:
    with pytest.raises(EventRejectedError) as exc:
        parse_message(value, headers)
    assert exc.value.reason == reason


def test_headers_to_dict_decodes_bytes() -> None:
    assert headers_to_dict([("a", b"1"), ("b", None)]) == {"a": "1", "b": ""}
    assert headers_to_dict(None) == {}
