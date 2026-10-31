"""Kafka consumer: validate, dedupe, persist, then commit offsets, then publish live updates.

Delivery is at-least-once (offsets are committed only after the database transaction commits);
the idempotent INSERT ... ON CONFLICT DO NOTHING makes redelivery harmless.

Run: python -m app.streaming.consumer
"""

import asyncio
import logging
import signal
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, request_id_var, tenant_id_var
from app.core.metrics import BATCH_DURATION, CONSUMER_LAG, EVENTS_CONSUMED
from app.streaming.dlq import DlqEntry, DlqPublisher
from app.streaming.event_parser import EventRejectedError, ParsedMessage, parse_message

logger = logging.getLogger(__name__)

RowKey = tuple[uuid.UUID, uuid.UUID]


@dataclass(frozen=True)
class ConsumedMessage:
    topic: str
    partition: int
    offset: int
    key: str | None
    value: bytes | None
    headers: dict[str, str]


class EventWriter(Protocol):
    async def insert_rows(self, rows: list[dict[str, Any]]) -> set[RowKey]: ...
    async def bucket_totals(
        self, tenant_id: uuid.UUID, store_id: uuid.UUID, bucket: datetime
    ) -> tuple[Decimal, int, str]: ...


# A unit of work: commits when the block exits cleanly, rolls back on error.
UnitOfWork = Callable[[], AbstractAsyncContextManager[EventWriter]]


class LiveNotifier(Protocol):
    async def notify(self, changes: list["BucketChange"]) -> None: ...


class TransientFailureError(Exception):
    """The database is unavailable; the batch must be retried, never dead-lettered."""


@dataclass(frozen=True)
class BucketChange:
    tenant_id: uuid.UUID
    store_id: uuid.UUID
    bucket: datetime
    delta_revenue: Decimal
    delta_units: int


@dataclass
class BatchOutcome:
    ok: int = 0
    duplicate: int = 0
    invalid: int = 0
    error: int = 0
    changes: list[BucketChange] = field(default_factory=list)


def is_transient(exc: BaseException) -> bool:
    """Connection-level problems are retried; anything else is a data problem."""
    if isinstance(
        exc, OSError | TimeoutError | ConnectionError | InterfaceError | OperationalError
    ):
        return True
    return isinstance(exc, DBAPIError) and bool(exc.connection_invalidated)


def hour_bucket(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def offsets_to_commit(messages: list[ConsumedMessage]) -> dict[tuple[str, int], int]:
    """Next offset to read per partition (last processed + 1)."""
    result: dict[tuple[str, int], int] = {}
    for m in messages:
        key = (m.topic, m.partition)
        result[key] = max(result.get(key, -1), m.offset + 1)
    return result


class BatchProcessor:
    def __init__(
        self,
        unit_of_work: UnitOfWork,
        dlq: DlqPublisher,
        max_attempts: int,
        backoff_seconds: tuple[float, ...],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._uow = unit_of_work
        self._dlq = dlq
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._sleep = sleep

    async def process(self, messages: list[ConsumedMessage]) -> BatchOutcome:
        """Persist a batch. Returns only after the DB commit (and DLQ writes) succeeded."""
        outcome = BatchOutcome()
        valid: list[tuple[ConsumedMessage, ParsedMessage]] = []
        dead: list[DlqEntry] = []

        for message in messages:
            try:
                valid.append((message, parse_message(message.value, message.headers)))
            except EventRejectedError as exc:
                dead.append(self._entry(message, exc.reason, exc.detail))
                outcome.invalid += 1

        # DLQ first: if this fails nothing is committed and the batch is redelivered.
        await self._dlq.send(dead)

        if valid:
            await self._persist(valid, outcome)
        return outcome

    # --- persistence ---------------------------------------------------------------------------

    async def _persist(
        self, valid: list[tuple[ConsumedMessage, ParsedMessage]], outcome: BatchOutcome
    ) -> None:
        rows = [row for _, parsed in valid for row in parsed.rows]
        inserted: set[RowKey] | None = None
        for attempt in range(self._max_attempts):
            try:
                async with self._uow() as writer:
                    inserted = await writer.insert_rows(rows)
                break
            except Exception as exc:
                if is_transient(exc):
                    if attempt + 1 < self._max_attempts:
                        await self._sleep(self._delay(attempt))
                        continue
                    raise TransientFailureError(type(exc).__name__) from exc
                logger.warning("batch_insert_failed; isolating messages", exc_info=True)
                break

        if inserted is None:
            await self._persist_individually(valid, outcome)
            return
        self._account(valid, inserted, outcome)

    async def _persist_individually(
        self, valid: list[tuple[ConsumedMessage, ParsedMessage]], outcome: BatchOutcome
    ) -> None:
        """A poison message must not block the partition: retry each alone, then dead-letter."""
        dead: list[DlqEntry] = []
        for message, parsed in valid:
            inserted: set[RowKey] | None = None
            last_error = ""
            for attempt in range(self._max_attempts):
                try:
                    async with self._uow() as writer:
                        inserted = await writer.insert_rows(parsed.rows)
                    break
                except Exception as exc:
                    if is_transient(exc):
                        raise TransientFailureError(type(exc).__name__) from exc
                    last_error = f"{type(exc).__name__}: {exc}"
                    await self._sleep(self._delay(attempt))
            if inserted is None:
                dead.append(self._entry(message, "processing_failed", last_error))
                outcome.error += 1
            else:
                self._account([(message, parsed)], inserted, outcome)
        await self._dlq.send(dead)

    def _account(
        self,
        valid: list[tuple[ConsumedMessage, ParsedMessage]],
        inserted: set[RowKey],
        outcome: BatchOutcome,
    ) -> None:
        deltas: dict[tuple[uuid.UUID, uuid.UUID, datetime], tuple[Decimal, int]] = defaultdict(
            lambda: (Decimal(0), 0)
        )
        for _, parsed in valid:
            new_rows = [r for r in parsed.rows if (r["event_id"], r["product_id"]) in inserted]
            if not new_rows:
                outcome.duplicate += 1
                continue
            outcome.ok += 1
            for row in new_rows:
                key = (row["tenant_id"], row["store_id"], hour_bucket(row["event_time"]))
                revenue, units = deltas[key]
                deltas[key] = (revenue + row["amount"], units + row["quantity"])
        outcome.changes.extend(
            BucketChange(tenant, store, bucket, revenue, units)
            for (tenant, store, bucket), (revenue, units) in deltas.items()
        )

    def _delay(self, attempt: int) -> float:
        return self._backoff[min(attempt, len(self._backoff) - 1)] if self._backoff else 0.0

    @staticmethod
    def _entry(message: ConsumedMessage, reason: str, detail: str) -> DlqEntry:
        return DlqEntry(
            key=message.key,
            value=message.value,
            original_headers=message.headers,
            reason=reason,
            detail=detail,
            topic=message.topic,
            partition=message.partition,
            offset=message.offset,
        )


class RedisLiveNotifier:
    """Post-commit, best-effort: totals come from the database, the source of truth."""

    def __init__(self, unit_of_work: UnitOfWork, publisher: Any) -> None:
        self._uow = unit_of_work
        self._publisher = publisher

    async def notify(self, changes: list[BucketChange]) -> None:
        from app.realtime.pubsub import LiveUpdate

        try:
            for change in changes:
                async with self._uow() as writer:
                    revenue, units, region = await writer.bucket_totals(
                        change.tenant_id, change.store_id, change.bucket
                    )
                await self._publisher.publish(
                    change.tenant_id,
                    LiveUpdate(
                        bucket=change.bucket,
                        store_id=change.store_id,
                        region=region,
                        revenue=revenue,
                        units=units,
                        delta_revenue=change.delta_revenue,
                        delta_units=change.delta_units,
                    ),
                )
            for tenant_id in {c.tenant_id for c in changes}:
                await self._publisher.mark_event_committed(tenant_id)
        except Exception:
            logger.warning("live_publish_failed", exc_info=True)  # never fails the batch


def record_metrics(outcome: BatchOutcome) -> None:
    for result in ("ok", "duplicate", "invalid", "error"):
        count = getattr(outcome, result)
        if count:
            EVENTS_CONSUMED.labels(result=result).inc(count)


# --- Kafka loop ----------------------------------------------------------------------------------


def _to_message(msg: Any) -> ConsumedMessage:
    from app.streaming.event_parser import headers_to_dict

    key = msg.key()
    return ConsumedMessage(
        topic=msg.topic(),
        partition=msg.partition(),
        offset=msg.offset(),
        key=key.decode() if key else None,
        value=msg.value(),
        headers=headers_to_dict(msg.headers()),
    )


def _update_lag(consumer: Any) -> None:
    try:
        for tp in consumer.position(consumer.assignment()):
            _, high = consumer.get_watermark_offsets(tp, cached=True)
            if tp.offset >= 0:
                CONSUMER_LAG.labels(partition=str(tp.partition)).set(max(0, high - tp.offset))
    except Exception:
        logger.debug("lag_update_failed", exc_info=True)


async def run_consumer(settings: Settings, stop: asyncio.Event) -> None:
    from confluent_kafka import Consumer, KafkaError, TopicPartition
    from redis.asyncio import Redis

    from app.db.session import make_engine, make_session_factory, session_scope
    from app.realtime.pubsub import LiveUpdatePublisher
    from app.repositories.sales_events import SalesEventRepository
    from app.streaming.producer import KafkaEventPublisher

    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)

    @asynccontextmanager
    async def unit_of_work() -> Any:
        async with session_scope(factory) as session:
            yield SalesEventRepository(session)

    redis = Redis.from_url(settings.redis_url)
    dlq_publisher = KafkaEventPublisher(
        settings.kafka_bootstrap_servers,
        settings.dlq_topic,
        settings.kafka_delivery_timeout_seconds,
    )
    processor = BatchProcessor(
        unit_of_work,
        DlqPublisher(dlq_publisher),
        settings.consumer_max_attempts,
        settings.consumer_backoff_seconds,
    )
    notifier = RedisLiveNotifier(unit_of_work, LiveUpdatePublisher(redis))

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.consumer_group,
            "enable.auto.commit": False,  # offsets are committed manually, after the DB commit
            "auto.offset.reset": "earliest",
            "partition.assignment.strategy": "cooperative-sticky",
        }
    )
    consumer.subscribe([settings.events_topic])
    logger.info("consumer_started", extra={"group": settings.consumer_group})
    last_lag = 0.0
    try:
        while not stop.is_set():
            raw = consumer.consume(
                num_messages=settings.consumer_batch_size,
                timeout=settings.consumer_batch_timeout_seconds,
            )
            if time.monotonic() - last_lag > 5:
                _update_lag(consumer)
                last_lag = time.monotonic()
            messages = []
            for msg in raw:
                err = msg.error()
                if err is not None:
                    if err.code() != KafkaError._PARTITION_EOF:
                        logger.error("kafka_error", extra={"error": str(err)})
                    continue
                messages.append(_to_message(msg))
            if not messages:
                continue

            def commit(offsets: dict[tuple[str, int], int]) -> None:
                consumer.commit(
                    offsets=[TopicPartition(t, p, o) for (t, p), o in offsets.items()],
                    asynchronous=False,
                )

            if await handle_batch(processor, messages, commit, notifier, stop, settings) is None:
                break  # stopped mid-batch: not committed, redelivered and deduplicated on restart
    finally:
        consumer.close()
        dlq_publisher.close()
        await redis.aclose()
        await engine.dispose()
        tenant_id_var.set(None)
        logger.info("consumer_stopped")


class _Stopped(Exception):
    pass


async def _process_with_pause(
    processor: BatchProcessor,
    messages: list[ConsumedMessage],
    stop: asyncio.Event,
    settings: Settings,
) -> BatchOutcome:
    """Database down: pause consuming and retry the same batch; never skip or dead-letter it."""
    delay = settings.consumer_backoff_seconds[-1] if settings.consumer_backoff_seconds else 1.0
    while True:
        try:
            return await processor.process(messages)
        except TransientFailureError:
            logger.error("database_unavailable; pausing", extra={"retry_in": delay})
        except Exception:
            # DLQ publish failure etc.: also retry the whole batch.
            logger.error("batch_failed; retrying", exc_info=True)
        for _ in range(int(delay * 10)):
            if stop.is_set():
                raise _Stopped
            await asyncio.sleep(0.1)


async def handle_batch(
    processor: BatchProcessor,
    messages: list[ConsumedMessage],
    commit: Callable[[dict[tuple[str, int], int]], None],
    notifier: LiveNotifier,
    stop: asyncio.Event,
    settings: Settings,
) -> BatchOutcome | None:
    """persist (DB commit) -> commit Kafka offsets -> publish live updates.

    Returns None if shutdown interrupted the batch before the database commit.
    """
    started = time.monotonic()
    request_id_var.set(messages[0].headers.get("request-id"))
    try:
        outcome = await _process_with_pause(processor, messages, stop, settings)
    except _Stopped:
        return None
    BATCH_DURATION.observe(time.monotonic() - started)
    record_metrics(outcome)
    try:
        commit(offsets_to_commit(messages))
    except Exception:
        logger.warning("offset_commit_failed; batch will be redelivered", exc_info=True)
        return outcome
    logger.info(
        "batch_committed",
        extra={
            "messages": len(messages),
            "ok": outcome.ok,
            "duplicate": outcome.duplicate,
            "invalid": outcome.invalid,
            "error": outcome.error,
            "request_ids": sorted({m.headers.get("request-id", "") for m in messages}),
        },
    )
    await notifier.notify(outcome.changes)
    return outcome


def main() -> None:
    from prometheus_client import start_http_server

    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.consumer_metrics_port)

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        await run_consumer(settings, stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
