"""Dead-letter handling: publishing failed messages and summarising what is in the DLQ."""

import logging
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from confluent_kafka import Consumer, TopicPartition

from app.core.config import Settings
from app.core.metrics import DLQ_BY_REASON, DLQ_TOTAL
from app.streaming.producer import EventPublisher, OutboundMessage

logger = logging.getLogger(__name__)

REASON_HEADER = "error-reason"
DETAIL_HEADER = "error-detail"


@dataclass(frozen=True)
class DlqEntry:
    key: str | None
    value: bytes | None
    original_headers: dict[str, str]
    reason: str
    detail: str
    topic: str
    partition: int
    offset: int


def to_outbound(entry: DlqEntry) -> OutboundMessage:
    headers = {
        **entry.original_headers,
        REASON_HEADER: entry.reason,
        DETAIL_HEADER: entry.detail[:500],
        "source-topic": entry.topic,
        "source-partition": str(entry.partition),
        "source-offset": str(entry.offset),
    }
    return OutboundMessage(key=entry.key or "unknown", value=entry.value or b"", headers=headers)


class DlqPublisher:
    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def send(self, entries: list[DlqEntry]) -> None:
        """Raises if the DLQ write is not acknowledged, so the batch is retried, not lost."""
        if not entries:
            return
        await self._publisher.publish([to_outbound(e) for e in entries])
        DLQ_TOTAL.inc(len(entries))
        for entry in entries:
            logger.warning(
                "dlq_message",
                extra={
                    "reason": entry.reason,
                    "source_partition": entry.partition,
                    "source_offset": entry.offset,
                    "request_id": entry.original_headers.get("request-id"),
                },
            )


def count_reasons(header_sets: Iterable[dict[str, str]]) -> dict[str, int]:
    return dict(Counter(h.get(REASON_HEADER, "unknown") for h in header_sets))


def summarize_dlq_topic(settings: Settings, max_messages: int = 100_000) -> dict[str, int]:
    """Read the DLQ from the beginning (without committing) and count messages by reason."""
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"dlq-summary-{uuid.uuid4().hex[:8]}",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    reasons: list[dict[str, str]] = []
    try:
        metadata = consumer.list_topics(settings.dlq_topic, timeout=5)
        partitions = list(metadata.topics[settings.dlq_topic].partitions)
        assignments = [TopicPartition(settings.dlq_topic, p, 0) for p in partitions]
        consumer.assign(assignments)
        remaining = {
            p: consumer.get_watermark_offsets(TopicPartition(settings.dlq_topic, p), timeout=5)[1]
            for p in partitions
        }
        positions = dict.fromkeys(partitions, 0)
        while len(reasons) < max_messages and any(positions[p] < remaining[p] for p in partitions):
            msg = consumer.poll(1.0)
            if msg is None:
                break
            offset = msg.offset()
            if msg.error() is not None or offset is None:
                continue
            positions[msg.partition()] = offset + 1
            raw_headers: Any = msg.headers() or []
            reasons.append(
                {
                    str(k): (v.decode() if isinstance(v, bytes) else str(v or ""))
                    for k, v in raw_headers
                }
            )
    finally:
        consumer.close()

    counts = count_reasons(reasons)
    DLQ_BY_REASON.clear()
    for reason, n in counts.items():
        DLQ_BY_REASON.labels(reason=reason).set(n)
    logger.info("dlq_summary", extra={"counts": counts})
    return counts
