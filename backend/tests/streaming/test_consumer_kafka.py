"""Consumer against real Kafka + TimescaleDB + Redis (testcontainers). Skipped without Docker.

Covers the Phase 3 acceptance criteria: exactly one row set for a duplicated event, a killed and
restarted consumer neither loses nor duplicates events, and a malformed message reaches the DLQ
while the partition keeps moving.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import asyncpg
import pytest
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic

from app.core.config import Settings
from app.streaming.consumer import run_consumer
from tests.integration.conftest import docker_available
from tests.unit.test_event_contract import make_event

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker is not available")

EVENTS_TOPIC = "sales.events.v1"
DLQ_TOPIC = "sales.events.dlq.v1"


@pytest.fixture(scope="module")
def kafka_bootstrap() -> Iterator[str]:
    from testcontainers.kafka import KafkaContainer

    with KafkaContainer() as kafka:
        bootstrap = kafka.get_bootstrap_server()
        admin = AdminClient({"bootstrap.servers": bootstrap})
        for future in admin.create_topics(
            [NewTopic(EVENTS_TOPIC, 6, 1), NewTopic(DLQ_TOPIC, 3, 1)]
        ).values():
            future.result(30)
        yield bootstrap


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    from testcontainers.redis import RedisContainer

    with RedisContainer() as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


def produce(
    bootstrap: str, messages: list[tuple[bytes, dict[str, str]]], key: str = "store"
) -> None:
    producer = Producer({"bootstrap.servers": bootstrap, "acks": "all"})
    for value, headers in messages:
        producer.produce(
            EVENTS_TOPIC,
            key=key.encode(),
            value=value,
            headers=[(k, v.encode()) for k, v in headers.items()],
        )
    assert producer.flush(30) == 0


def event_message(
    tenant: uuid.UUID, event: dict[str, Any] | None = None
) -> tuple[bytes, dict[str, str]]:
    payload = event or make_event()
    return json.dumps(payload).encode(), {
        "tenant-id": str(tenant),
        "request-id": f"req-{uuid.uuid4().hex[:8]}",
    }


@pytest.fixture
def settings_for(kafka_bootstrap: str, redis_url: str, timescale_url: str) -> Any:
    def build() -> Settings:
        return Settings(
            database_url=timescale_url,
            redis_url=redis_url,
            kafka_bootstrap_servers=kafka_bootstrap,
            consumer_group=f"test-{uuid.uuid4().hex[:8]}",
            consumer_batch_size=20,
            consumer_batch_timeout_seconds=0.5,
            consumer_backoff_seconds=(0.1,),
            consumer_metrics_port=0,
        )

    return build


async def count_rows(pg: asyncpg.Connection, tenant: uuid.UUID) -> int:
    return int(await pg.fetchval("SELECT count(*) FROM sales_events WHERE tenant_id=$1", tenant))


async def wait_for(predicate: Any, timeout_seconds: float = 45.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.3)
    raise AssertionError("condition not reached in time")


@pytest.fixture
async def pg(timescale_url: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(timescale_url)
    yield conn
    await conn.close()


async def test_duplicate_event_is_stored_once(
    kafka_bootstrap: str, settings_for: Any, pg: asyncpg.Connection
) -> None:
    tenant = uuid.uuid4()
    message = event_message(tenant)
    produce(kafka_bootstrap, [message, message])  # same event delivered twice
    settings = settings_for()
    stop = asyncio.Event()
    task = asyncio.create_task(run_consumer(settings, stop))
    try:
        await wait_for(lambda: _rows_at_least(pg, tenant, 2))
        await asyncio.sleep(2)  # give a (wrong) duplicate time to appear
        assert await count_rows(pg, tenant) == 2  # one event x 2 line items, not 4
    finally:
        stop.set()
        await task


async def _rows_at_least(pg: asyncpg.Connection, tenant: uuid.UUID, n: int) -> bool:
    return await count_rows(pg, tenant) >= n


async def test_malformed_message_lands_in_dlq_and_partition_keeps_moving(
    kafka_bootstrap: str, settings_for: Any, pg: asyncpg.Connection
) -> None:
    tenant = uuid.uuid4()
    headers = {"tenant-id": str(tenant), "request-id": "req-bad"}
    produce(kafka_bootstrap, [(b"{definitely not json", headers), event_message(tenant)])
    settings = settings_for()
    stop = asyncio.Event()
    task = asyncio.create_task(run_consumer(settings, stop))
    try:
        await wait_for(lambda: _rows_at_least(pg, tenant, 2))  # the valid event behind it landed
    finally:
        stop.set()
        await task

    dlq = Consumer(
        {
            "bootstrap.servers": kafka_bootstrap,
            "group.id": f"dlq-{uuid.uuid4().hex[:6]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    dlq.subscribe([DLQ_TOPIC])
    found: dict[str, str] = {}
    for _ in range(40):
        msg = dlq.poll(1.0)
        if msg is not None and msg.error() is None:
            hdrs = {k: (v or b"").decode() for k, v in msg.headers() or []}
            if hdrs.get("request-id") == "req-bad":
                found = hdrs
                break
    dlq.close()
    assert found["error-reason"] == "invalid_json"
    assert found["source-topic"] == EVENTS_TOPIC


async def test_killed_and_restarted_consumer_loses_and_duplicates_nothing(
    kafka_bootstrap: str, settings_for: Any, pg: asyncpg.Connection
) -> None:
    tenant = uuid.uuid4()
    events = [make_event() for _ in range(60)]
    produce(kafka_bootstrap, [event_message(tenant, e) for e in events])
    settings = settings_for()  # same consumer group across both runs

    first = asyncio.create_task(run_consumer(settings, asyncio.Event()))
    await wait_for(lambda: _rows_at_least(pg, tenant, 2))
    first.cancel()  # hard kill mid-stream: no graceful shutdown, offsets may be behind the DB
    with pytest.raises(asyncio.CancelledError):
        await first

    stop = asyncio.Event()
    second = asyncio.create_task(run_consumer(settings, stop))
    try:
        await wait_for(lambda: _rows_at_least(pg, tenant, 120))
        await asyncio.sleep(2)
    finally:
        stop.set()
        await second
    assert await count_rows(pg, tenant) == 120  # 60 events x 2 items: no loss, no duplicates
    distinct = await pg.fetchval(
        "SELECT count(DISTINCT event_id) FROM sales_events WHERE tenant_id=$1", tenant
    )
    assert distinct == 60
