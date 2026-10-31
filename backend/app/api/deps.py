import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import tenant_id_var
from app.core.security import InvalidTokenError, decode_access_token, hash_api_key
from app.realtime.pubsub import RedisFreshness
from app.realtime.websocket_manager import ConnectionManager
from app.repositories.analytics import AnalyticsRepository
from app.repositories.exports import ExportJobRepository
from app.repositories.products import ProductRepository
from app.repositories.stores import StoreIdentity, StoreRepository
from app.repositories.users import UserRepository
from app.services.analytics import AnalyticsService
from app.services.auth import AuthService
from app.services.catalog import CatalogService
from app.services.exports import CsvStreamService, ExportQueue, ExportService
from app.services.health import CheckFn, build_checks
from app.services.ingestion import IngestionService
from app.services.rate_limit import RateLimiter
from app.storage.s3 import ObjectStore
from app.streaming.producer import EventPublisher

_bearer = HTTPBearer(auto_error=False, description="JWT from `POST /api/v1/auth/login`.")


# --- app state -------------------------------------------------------------------------------


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_readiness_checks(request: Request) -> Mapping[str, CheckFn]:
    """Dependency probes for /health/ready. Tests override this to mock the checks."""
    return build_checks(get_app_settings(request))


def get_readiness_timeout(request: Request) -> float:
    return get_app_settings(request).health_check_timeout_seconds


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_publisher(request: Request) -> EventPublisher:
    return cast(EventPublisher, request.app.state.publisher)


def get_object_store(request: Request) -> ObjectStore:
    return cast(ObjectStore, request.app.state.object_store)


def get_export_queue(request: Request) -> ExportQueue:
    return cast(ExportQueue, request.app.state.export_queue)


def get_ws_manager(request: Request) -> ConnectionManager:
    return cast(ConnectionManager, request.app.state.ws_manager)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", request.app.state.session_factory)


async def get_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]


# --- dashboard user auth (JWT) ---------------------------------------------------------------


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    role: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: SettingsDep,
) -> CurrentUser:
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        claims = decode_access_token(credentials.credentials, settings.jwt_secret)
        user = CurrentUser(
            id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tenant_id"]),
            role=str(claims["role"]),
        )
    except (InvalidTokenError, KeyError, ValueError):
        raise _unauthorized("Invalid or expired token") from None
    tenant_id_var.set(str(user.tenant_id))
    return user


UserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*roles: str) -> Callable[[CurrentUser], CurrentUser]:
    def checker(user: UserDep) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return checker


AnyRole = Annotated[CurrentUser, Depends(require_roles("viewer", "analyst", "admin"))]
AnalystRole = Annotated[CurrentUser, Depends(require_roles("analyst", "admin"))]
AdminRole = Annotated[CurrentUser, Depends(require_roles("admin"))]


# --- producer API keys -----------------------------------------------------------------------


async def get_ingest_identity(
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    x_api_key: Annotated[
        str | None, Header(description="Store API key; only its SHA-256 hash is stored.")
    ] = None,
) -> StoreIdentity:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key")
    identity = await StoreRepository(session).resolve_api_key_hash(hash_api_key(x_api_key))
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    tenant_id_var.set(str(identity.tenant_id))

    limiter = RateLimiter(redis, settings.ingest_rate_per_second, settings.ingest_rate_burst)
    result = await limiter.check(f"ingest:{identity.store_id}")
    if not result.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )
    return identity


IngestIdentity = Annotated[StoreIdentity, Depends(get_ingest_identity)]


# --- services --------------------------------------------------------------------------------


def get_ingestion_service(
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
) -> IngestionService:
    return IngestionService(publisher)


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(UserRepository(session), settings.jwt_secret, settings.jwt_expire_minutes)


def get_analytics_service(
    session: SessionDep, redis: RedisDep, settings: SettingsDep
) -> AnalyticsService:
    return AnalyticsService(
        AnalyticsRepository(session), RedisFreshness(redis), UserRepository(session), settings
    )


def get_catalog_service(session: SessionDep) -> CatalogService:
    return CatalogService(StoreRepository(session), ProductRepository(session))


def get_export_service(
    session: SessionDep,
    queue: Annotated[ExportQueue, Depends(get_export_queue)],
    storage: Annotated[ObjectStore, Depends(get_object_store)],
    settings: SettingsDep,
) -> ExportService:
    return ExportService(ExportJobRepository(session), queue, storage, settings)


def get_csv_stream_service(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    settings: SettingsDep,
) -> CsvStreamService:
    return CsvStreamService(factory, settings)
