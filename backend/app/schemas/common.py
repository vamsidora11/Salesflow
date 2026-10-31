from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    detail: str = Field(description="Human-readable error message.")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - explicit Generic keeps FastAPI schema naming
    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to fetch the next page; null on the last page."
    )
