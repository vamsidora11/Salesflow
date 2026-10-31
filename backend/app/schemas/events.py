"""Event contract, version 1 (plan section 5). Any field change requires a version bump."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SUPPORTED_VERSIONS = frozenset({1})
CENT = Decimal("0.01")

Channel = Literal["pos", "online", "mobile"]


class SaleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: Annotated[int, Field(gt=0, le=100_000)]
    # Money travels as a string in JSON and is a Decimal in Python (never a float).
    unit_price: Annotated[
        Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    ]

    @field_validator("unit_price", mode="before")
    @classmethod
    def _reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("money must be a string or integer, not a float")
        return value

    @property
    def amount(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(CENT)


class SaleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    event_type: Literal["sale.completed"] = "sale.completed"
    event_version: int = 1
    event_time: datetime
    store_id: uuid.UUID
    channel: Channel
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    items: Annotated[list[SaleItem], Field(min_length=1, max_length=200)]

    @field_validator("event_time")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event_time must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _unique_products(self) -> "SaleEvent":
        # One stored row per (event, product); repeated products must be merged by the producer.
        ids = [item.product_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("items must not repeat a product_id; merge quantities instead")
        return self


class RejectedEvent(BaseModel):
    index: int = Field(description="Position of the event in the request.")
    event_id: str | None = None
    error: str


class IngestRequest(BaseModel):
    events: Annotated[list[dict[str, Any]], Field(min_length=1)]


class IngestResponse(BaseModel):
    accepted: int = Field(description="Events written to the durable log (not yet in the DB).")
    rejected: int
    rejected_events: list[RejectedEvent]

    model_config = {
        "json_schema_extra": {
            "example": {
                "accepted": 1,
                "rejected": 1,
                "rejected_events": [
                    {
                        "index": 1,
                        "event_id": "7b1e4c2a-9d3f-4e8a-b6c1-2f5d7a9e0c11",
                        "error": "store_id does not match the API key's store",
                    }
                ],
            }
        }
    }
