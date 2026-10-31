from typing import Literal

from pydantic import BaseModel, Field


class LivenessResponse(BaseModel):
    status: Literal["ok"] = Field(description="Always `ok` while the process is serving requests.")


class DependencyStatus(BaseModel):
    status: Literal["ok", "unreachable"]
    error: str | None = Field(
        default=None,
        description="Exception class name only; messages are omitted to avoid leaking credentials.",
    )


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    unreachable: list[str] = Field(description="Names of dependencies that failed their probe.")
    checks: dict[str, DependencyStatus]

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "unavailable",
                "unreachable": ["redis"],
                "checks": {
                    "postgres": {"status": "ok", "error": None},
                    "redis": {"status": "unreachable", "error": "ConnectionError"},
                    "kafka": {"status": "ok", "error": None},
                },
            }
        }
    }
