import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExportFilters(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    date_from: datetime = Field(alias="from", description="Inclusive start (naive = UTC).")
    date_to: datetime = Field(alias="to", description="Exclusive end (naive = UTC).")
    granularity: Literal["hour", "day"] = "day"
    store_id: uuid.UUID | None = None
    region: str | None = Field(default=None, max_length=100)
    channel: Literal["pos", "online", "mobile"] | None = None


class ExportCreate(BaseModel):
    format: Literal["csv", "xlsx"]
    filters: ExportFilters


class ExportJobResponse(BaseModel):
    id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "expired"]
    format: Literal["csv", "xlsx"]
    row_count: int | None = Field(description="Rows written so far (final once succeeded).")
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None


class DownloadResponse(BaseModel):
    url: str = Field(description="Presigned URL. Treat as a secret; it is not logged.")
    expires_in: int = Field(description="Seconds until the URL stops working.")
