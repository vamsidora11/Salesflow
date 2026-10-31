import asyncio
from collections.abc import Awaitable, Callable, Mapping

import asyncpg
from confluent_kafka.admin import AdminClient
from redis.asyncio import Redis

from app.core.config import Settings
from app.schemas.health import DependencyStatus, ReadinessResponse

# A dependency check returns normally when the dependency is reachable and raises otherwise.
CheckFn = Callable[[], Awaitable[None]]


async def check_postgres(settings: Settings) -> None:
    conn = await asyncpg.connect(
        settings.database_url, timeout=settings.health_check_timeout_seconds
    )
    try:
        await conn.execute("SELECT 1")
    finally:
        await conn.close()


async def check_redis(settings: Settings) -> None:
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.health_check_timeout_seconds,
        socket_timeout=settings.health_check_timeout_seconds,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def check_kafka(settings: Settings) -> None:
    def probe() -> None:
        admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
        # Raises KafkaException when no broker answers within the timeout.
        admin.list_topics(timeout=settings.health_check_timeout_seconds)

    await asyncio.to_thread(probe)


def build_checks(settings: Settings) -> dict[str, CheckFn]:
    return {
        "postgres": lambda: check_postgres(settings),
        "redis": lambda: check_redis(settings),
        "kafka": lambda: check_kafka(settings),
    }


async def _run_check(
    name: str, check: CheckFn, timeout_seconds: float
) -> tuple[str, DependencyStatus]:
    try:
        # Slightly above the per-client timeout so the client's own error wins when it can.
        await asyncio.wait_for(check(), timeout=timeout_seconds + 1.0)
    except Exception as exc:
        return name, DependencyStatus(status="unreachable", error=type(exc).__name__)
    return name, DependencyStatus(status="ok")


async def run_readiness(checks: Mapping[str, CheckFn], timeout_seconds: float) -> ReadinessResponse:
    """Run every dependency check concurrently and report which ones are unreachable."""
    results = await asyncio.gather(
        *(_run_check(n, fn, timeout_seconds) for n, fn in checks.items())
    )
    statuses = dict(results)
    unreachable = [name for name, s in statuses.items() if s.status != "ok"]
    return ReadinessResponse(
        status="unavailable" if unreachable else "ready",
        unreachable=unreachable,
        checks=statuses,
    )
