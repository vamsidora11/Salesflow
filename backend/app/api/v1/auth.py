from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_auth_service
from app.api.responses import UNAUTHORIZED
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.auth import AuthService, InvalidCredentialsError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    summary="Log in and receive a JWT",
    response_model=TokenResponse,
    responses=UNAUTHORIZED,
)
async def login(
    body: LoginRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> TokenResponse:
    try:
        return await service.login(body)
    except InvalidCredentialsError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        ) from None
