"""BatchProcessor / handle_batch behaviour with an in-memory unit of work (no Kafka, no DB)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import Settings
from app.streaming.consumer import (
    BatchProcessor,
    ConsumedMessage,
    TransientFailureError,
    handle_batch,
    hour_bucket,
    is_transient,
    offsets_to_commit,
)
from app.streaming.dlq import DlqPublisher
from app.streaming.producer import OutboundMessage
from tests.unit.test_event_contract import make_event

TENANT = uuid.uuid4()


class Recorder:
    """Shared, ordered log of side effects so tests can assert sequencing."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.rows: dict[tuple[Any, Any], dict[str, Any]] = {}
        self.fail_with: list[Exception] = []  # popped one per insert call
        self.poison_event_ids: set[uuid.UUID] = set()
        self.dlq: list[OutboundMessage] = []

    @asynccontextmanager
    async def uow(self) -> AsyncIterator["Recorder"]:
        yield self
        self.log.append("db_commit")

    async def insert_rows(self, rows: list[dict[str, Any]]) -> set[tuple[Any, Any]]:
        self.log.append("db_insert")
        if self.fail_with:
            raise self.fail_with.pop(0)
        if any(r["event_id"] in self.poison_event_ids for r in rows):
            raise IntegrityError("insert", {}, Exception("bad row"))
        new = set()
        for r in rows:
            key = (r["event_id"], r["product_id"])
            if key not in self.rows:  # ON CONFLICT DO NOTHING
                self.rows[key] = r
                new.add(key)
        return new

    async def publish(self, messages: list[OutboundMessage]) -> None:
        self.log.append("dlq_publish")
        self.dlq.extend(messages)


async def no_sleep(_: float) -> None:
    return None


def make_processor(rec: Recorder, attempts: int = 3) -> BatchProcessor:
    return BatchProcessor(rec.uow, DlqPublisher(rec), attempts, (0.0,), sleep=no_sleep)  # type: ignore[arg-type]


def message(
    offset: int, value: bytes | None = None, partition: int = 0, **event: Any
) -> ConsumedMessage:
    payload = value if value is not None else json.dumps(make_event(**event)).encode()
    return ConsumedMessage(
        "sales.events.v1",
        partition,
        offset,
        "store-key",
        payload,
        {"tenant-id": str(TENANT), "request-id": f"req-{offset}"},
    )


async def test_valid_messages_are_persisted_and_counted() -> None:
    rec = Recorder()
    outcome = await make_processor(rec).process([message(0), message(1)])
    assert (outcome.ok, outcome.duplicate, outcome.invalid, outcome.error) == (2, 0, 0, 0)
    assert len(rec.rows) == 4  # 2 events x 2 line items
    assert rec.dlq == []


async def test_same_event_delivered_twice_results_in_one_set_of_rows() -> None:
    rec = Recorder()
    first = message(0)
    redelivered = ConsumedMessage(**{**first.__dict__, "offset": 7})
    processor = make_processor(rec)
    await processor.process([first])
    outcome = await processor.process([redelivered])
    assert len(rec.rows) == 2  # not 4
    assert outcome.duplicate == 1 and outcome.ok == 0
    assert outcome.changes == []  # duplicates must not produce live-update deltas


async def test_invalid_message_goes_to_dlq_and_valid_ones_still_persist() -> None:
    rec = Recorder()
    outcome = await make_processor(rec).process(
        [message(0), message(1, value=b"{broken"), message(2, channel="fax"), message(3)]
    )
    assert outcome.ok == 2 and outcome.invalid == 2
    reasons = sorted(m.headers["error-reason"] for m in rec.dlq)
    assert reasons == ["invalid_json", "invalid_schema"]
    dlq_msg = rec.dlq[0]
    assert dlq_msg.headers["source-topic"] == "sales.events.v1"
    assert dlq_msg.headers["source-offset"] in {"1", "2"}
    assert dlq_msg.headers["tenant-id"] == str(TENANT)  # original headers preserved
    assert len(rec.rows) == 4


async def test_dlq_is_written_before_the_database_insert() -> None:
    rec = Recorder()
    await make_processor(rec).process([message(0), message(1, value=b"x")])
    assert rec.log.index("dlq_publish") < rec.log.index("db_insert")


async def test_poison_message_is_isolated_and_dead_lettered_without_blocking_others() -> None:
    rec = Recorder()
    poison = message(1)
    rec.poison_event_ids.add(uuid.UUID(json.loads(poison.value or b"{}")["event_id"]))
    outcome = await make_processor(rec).process([message(0), poison, message(2)])
    assert outcome.ok == 2 and outcome.error == 1
    assert [m.headers["error-reason"] for m in rec.dlq] == ["processing_failed"]
    assert len(rec.rows) == 4


async def test_transient_database_errors_are_retried_then_succeed() -> None:
    rec = Recorder()
    rec.fail_with = [OperationalError("insert", {}, Exception("conn")), OSError("reset")]
    outcome = await make_processor(rec, attempts=3).process([message(0)])
    assert outcome.ok == 1
    assert rec.dlq == []
    assert rec.log.count("db_insert") == 3


async def test_persistent_database_outage_raises_and_never_dead_letters() -> None:
    rec = Recorder()
    rec.fail_with = [OSError("down")] * 5
    with pytest.raises(TransientFailureError):
        await make_processor(rec, attempts=3).process([message(0), message(1)])
    assert rec.dlq == []
    assert rec.rows == {}


async def test_live_update_deltas_are_grouped_per_store_and_hour() -> None:
    rec = Recorder()
    store = str(uuid.uuid4())
    outcome = await make_processor(rec).process(
        [
            message(0, store_id=store, event_time="2026-10-30T14:05:00Z"),
            message(1, store_id=store, event_time="2026-10-30T14:55:00Z"),
            message(2, store_id=store, event_time="2026-10-30T15:01:00Z"),
        ]
    )
    by_hour = {c.bucket.hour: c for c in outcome.changes}
    assert set(by_hour) == {14, 15}
    assert by_hour[14].delta_revenue == Decimal("89.96")  # 2 events x (39.98 + 5.00)
    assert by_hour[14].delta_units == 6
    assert by_hour[15].delta_revenue == Decimal("44.98")


# --- offsets / ordering ---------------------------------------------------------------------


def test_offsets_to_commit_is_last_offset_plus_one_per_partition() -> None:
    msgs = [message(5, partition=0), message(9, partition=0), message(2, partition=1)]
    assert offsets_to_commit(msgs) == {("sales.events.v1", 0): 10, ("sales.events.v1", 1): 3}


class FakeNotifier:
    def __init__(self, rec: Recorder) -> None:
        self._rec = rec

    async def notify(self, changes: list[Any]) -> None:
        self._rec.log.append("redis_publish")


async def test_offsets_commit_only_after_db_commit_and_before_live_publish() -> None:
    rec = Recorder()
    committed: list[dict[tuple[str, int], int]] = []

    def commit(offsets: dict[tuple[str, int], int]) -> None:
        rec.log.append("kafka_commit")
        committed.append(offsets)

    outcome = await handle_batch(
        make_processor(rec),
        [message(0), message(1)],
        commit,
        FakeNotifier(rec),
        asyncio.Event(),
        Settings(),
    )
    assert outcome is not None
    assert (
        rec.log.index("db_commit") < rec.log.index("kafka_commit") < rec.log.index("redis_publish")
    )
    assert committed == [{("sales.events.v1", 0): 2}]


async def test_no_offset_commit_when_database_commit_never_happens() -> None:
    rec = Recorder()
    rec.fail_with = [OSError("down")] * 100
    stop = asyncio.Event()
    committed: list[Any] = []
    settings = Settings(consumer_backoff_seconds=(0.1,))

    task = asyncio.create_task(
        handle_batch(
            make_processor(rec), [message(0)], committed.append, FakeNotifier(rec), stop, settings
        )
    )
    await asyncio.sleep(0.3)
    stop.set()  # SIGTERM arrives while the database is down
    assert await task is None
    assert committed == []
    assert "kafka_commit" not in rec.log


async def test_failed_kafka_commit_is_survivable_and_skips_live_publish() -> None:
    rec = Recorder()

    def commit(_: dict[tuple[str, int], int]) -> None:
        raise RuntimeError("rebalance")

    outcome = await handle_batch(
        make_processor(rec), [message(0)], commit, FakeNotifier(rec), asyncio.Event(), Settings()
    )
    assert outcome is not None and outcome.ok == 1
    assert "redis_publish" not in rec.log  # batch will be redelivered and deduplicated


# --- classification ---------------------------------------------------------------------------


def test_is_transient_classification() -> None:
    assert is_transient(OSError("x"))
    assert is_transient(TimeoutError())
    assert is_transient(OperationalError("s", {}, Exception("c")))
    assert not is_transient(IntegrityError("s", {}, Exception("dup")))
    assert not is_transient(ValueError("bad"))


def test_hour_bucket_truncates() -> None:
    assert hour_bucket(datetime.fromisoformat("2026-10-30T14:59:59.9+00:00")).minute == 0
