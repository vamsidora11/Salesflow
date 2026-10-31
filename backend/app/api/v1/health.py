from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import get_readiness_checks, get_readiness_timeout
from app.schemas.health import LivenessResponse, ReadinessResponse
from app.services.health import CheckFn, run_readiness

# Mounted at the root (no /api/v1 prefix) so orchestrators and nginx can probe /health.
router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Liveness probe",
    description="Returns 200 while the process is up. Does not touch any dependency.",
    response_model=LivenessResponse,
)
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/health/ready",
    summary="Readiness probe",
    description=(
        "Checks PostgreSQL, Redis and Kafka. Returns 200 only when all are reachable; "
        "otherwise 503 with the unreachable dependencies listed by name."
    ),
    response_model=ReadinessResponse,
    responses={
        503: {
            "model": ReadinessResponse,
            "description": "One or more dependencies are unreachable (named in `unreachable`).",
        }
    },
)
async def readiness(
    checks: Annotated[Mapping[str, CheckFn], Depends(get_readiness_checks)],
    timeout_seconds: Annotated[float, Depends(get_readiness_timeout)],
) -> JSONResponse:
    report = await run_readiness(checks, timeout_seconds)
    return JSONResponse(
        status_code=200 if report.status == "ready" else 503,
        content=report.model_dump(mode="json"),
    )
