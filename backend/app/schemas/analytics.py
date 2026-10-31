import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["pos", "online", "mobile"]


class Meta(BaseModel):
    generated_at: datetime
    data_freshness_seconds: float | None = Field(
        description="Seconds since the consumer last committed an event for this tenant; "
        "null if unknown."
    )
    currency: str = Field(description="ISO 4217 code; all money values use this currency.")


class SummaryResponse(BaseModel):
    revenue: Decimal = Field(description="Money as a string.")
    units: int
    transactions: int
    average_order_value: Decimal
    meta: Meta


class TimeseriesPoint(BaseModel):
    bucket: datetime
    value: Decimal


class TimeseriesResponse(BaseModel):
    granularity: Literal["hour", "day"]
    metric: Literal["revenue", "units"]
    points: list[TimeseriesPoint]
    meta: Meta


class TopProduct(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    revenue: Decimal
    units: int


class TopProductsResponse(BaseModel):
    items: list[TopProduct]
    meta: Meta


class DimensionBreakdown(BaseModel):
    label: str
    revenue: Decimal
    units: int
    transactions: int


class BreakdownResponse(BaseModel):
    items: list[DimensionBreakdown]
    meta: Meta
