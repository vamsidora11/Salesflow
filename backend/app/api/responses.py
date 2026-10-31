from typing import Any

from app.schemas.common import ErrorResponse

UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, invalid or expired credentials."}
}
FORBIDDEN: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "The user's role does not allow this action."}
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Resource does not exist for this tenant."}
}
INVALID_RANGE: dict[int | str, dict[str, Any]] = {
    422: {"description": "Validation error, e.g. missing or oversized time range."}
}

AUTHED = {**UNAUTHORIZED, **FORBIDDEN}
