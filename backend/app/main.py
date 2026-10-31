import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

from app.api.v1 import analytics, auth, catalog, exports, health, ingest, ws
from app.core.config import Settings, get_settings
from app.core.logging import (
    configure_logging,
    request_id_var,
    sanitize_request_id,
    tenant_id_var,
)
from app.db.session import make_engine, make_session_factory
from app.realtime.pubsub import RedisFanout
from app.realtime.websocket_manager import ConnectionManager
from app.services.analytics import InvalidRangeError
from app.services.auth import InvalidCredentialsError
from app.services.catalog import InvalidCursorError
from app.services.exports import (
    ExportExpiredError,
    ExportNotFoundError,
    ExportNotReadyError,
    ExportTooLargeError,
)
from app.storage.s3 import S3ObjectStore
from app.streaming.producer import KafkaEventPublisher, KafkaUnavailableError
from app.tasks.celery_app import CeleryExportQueue

logger = logging.getLogger(__name__)

API_DESCRIPTION = """
Real-time sales analytics platform.

Events flow API -> Kafka -> consumer -> TimescaleDB -> Redis pub/sub -> WebSocket -> dashboard.

* Producers authenticate with `X-API-Key` (`/ingest`); dashboard users with a JWT bearer token.
* All data endpoints are tenant-scoped. Another tenant's resource returns 404.
* Money is a string in JSON. Timestamps are UTC; naive query timestamps are read as UTC.
* WebSocket messages are not part of OpenAPI; see `docs/websocket.md`.
"""

TAGS_METADATA = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "auth", "description": "Dashboard user login."},
    {"name": "ingest", "description": "Producer-facing event ingestion (API key)."},
    {"name": "analytics", "description": "KPIs and charts, read from continuous aggregates."},
    {"name": "exports", "description": "CSV streaming and asynchronous XLSX exports."},
    {"name": "stores", "description": "Store lookups and administration."},
    {"name": "products", "description": "Product lookups and administration."},
]


def _operation_id(route: APIRoute) -> str:
    """Stable operation IDs (`<tag>_<function>`) so generated clients do not churn."""
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


def _error(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code, headers=headers)


def _register_exception_handlers(app: FastAPI) -> None:
    settings: Settings = app.state.settings

    @app.exception_handler(InvalidRangeError)
    async def _range(_: Request, exc: InvalidRangeError) -> JSONResponse:
        return _error(422, str(exc))

    @app.exception_handler(InvalidCursorError)
    async def _cursor(_: Request, exc: InvalidCursorError) -> JSONResponse:
        return _error(422, str(exc))

    @app.exception_handler(ExportNotFoundError)
    async def _not_found(_: Request, __: ExportNotFoundError) -> JSONResponse:
        return _error(404, "Export not found")

    @app.exception_handler(ExportNotReadyError)
    async def _not_ready(_: Request, __: ExportNotReadyError) -> JSONResponse:
        return _error(409, "Export is not finished yet")

    @app.exception_handler(ExportExpiredError)
    async def _expired(_: Request, __: ExportExpiredError) -> JSONResponse:
        return _error(410, "Export has expired")

    @app.exception_handler(ExportTooLargeError)
    async def _too_large(_: Request, exc: ExportTooLargeError) -> JSONResponse:
        return _error(413, str(exc))

    @app.exception_handler(InvalidCredentialsError)
    async def _credentials(_: Request, __: InvalidCredentialsError) -> JSONResponse:
        return _error(401, "Invalid email or password", {"WWW-Authenticate": "Bearer"})

    @app.exception_handler(KafkaUnavailableError)
    async def _kafka(_: Request, __: KafkaUnavailableError) -> JSONResponse:
        logger.error("kafka_unavailable")
        return _error(
            503,
            "Event log temporarily unavailable; retry later",
            {"Retry-After": str(settings.kafka_retry_after_seconds)},
        )


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = sanitize_request_id(request.headers.get("x-request-id"))
        request_id_var.set(request_id)
        tenant_id_var.set(None)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path not in ("/health", "/metrics"):
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,  # no query string: it may carry sensitive filters
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = make_engine(settings.database_url)
    owned: dict[str, Any] = {}

    def provide(name: str, factory: Callable[[], Any]) -> None:
        # Tests pre-populate app.state with fakes; only build what is missing.
        if not hasattr(app.state, name):
            owned[name] = factory()
            setattr(app.state, name, owned[name])

    provide("session_factory", lambda: make_session_factory(engine))
    provide("redis", lambda: Redis.from_url(settings.redis_url))
    provide(
        "publisher",
        lambda: KafkaEventPublisher(
            settings.kafka_bootstrap_servers,
            settings.events_topic,
            settings.kafka_delivery_timeout_seconds,
        ),
    )
    provide("object_store", lambda: S3ObjectStore(settings))
    provide("export_queue", CeleryExportQueue)

    fanout_task: asyncio.Task[None] | None = None
    if settings.realtime_enabled:
        fanout_task = asyncio.create_task(RedisFanout(app.state.redis, app.state.ws_manager).run())
    try:
        yield
    finally:
        if fanout_task is not None:
            fanout_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await fanout_task
        if "publisher" in owned:
            owned["publisher"].close()
        if "redis" in owned:
            await owned["redis"].aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="SalesFlow API",
        version="0.1.0",
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        generate_unique_id_function=_operation_id,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.ws_manager = ConnectionManager()
    configure_logging(settings.log_level)
    _register_exception_handlers(app)
    _register_middleware(app)

    app.include_router(health.router)
    prefix = "/api/v1"
    app.include_router(auth.router, prefix=prefix)
    app.include_router(ingest.router, prefix=prefix)
    app.include_router(analytics.router, prefix=prefix)
    app.include_router(exports.router, prefix=prefix)
    app.include_router(catalog.stores_router, prefix=prefix)
    app.include_router(catalog.products_router, prefix=prefix)
    app.include_router(ws.router, prefix=prefix)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
