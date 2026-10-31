"""confluent-kafka producer wrapper (infrastructure adapter, called from services only)."""

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from confluent_kafka import KafkaError, KafkaException, Producer


class KafkaUnavailableError(Exception):
    """The broker did not durably acknowledge the messages; callers should return 503."""


@dataclass(frozen=True)
class OutboundMessage:
    key: str
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)


class EventPublisher(Protocol):
    async def publish(self, messages: list[OutboundMessage]) -> None: ...


class KafkaEventPublisher:
    """Idempotent, acks=all producer with a bounded in-memory queue (no unbounded buffering)."""

    def __init__(self, bootstrap_servers: str, topic: str, delivery_timeout_seconds: float) -> None:
        self._topic = topic
        self._timeout = delivery_timeout_seconds
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "acks": "all",
                "enable.idempotence": True,
                "queue.buffering.max.messages": 100_000,
                "message.timeout.ms": int(delivery_timeout_seconds * 1000),
                "linger.ms": 5,
            }
        )

    async def publish(self, messages: list[OutboundMessage]) -> None:
        await asyncio.to_thread(self._publish_blocking, messages)

    def _publish_blocking(self, messages: list[OutboundMessage]) -> None:
        errors: list[KafkaError] = []
        lock = threading.Lock()

        def on_delivery(err: KafkaError | None, _msg: Any) -> None:
            if err is not None:
                with lock:
                    errors.append(err)

        try:
            for message in messages:
                self._producer.produce(
                    self._topic,
                    key=message.key.encode(),
                    value=message.value,
                    headers=[(k, v.encode()) for k, v in message.headers.items()],
                    on_delivery=on_delivery,
                )
                self._producer.poll(0)
            remaining = self._producer.flush(self._timeout + 1.0)
        except (BufferError, KafkaException) as exc:
            raise KafkaUnavailableError(type(exc).__name__) from exc
        if remaining > 0 or errors:
            # Undelivered messages stay queued in librdkafka; purge so retries do not duplicate.
            self._producer.purge()
            self._producer.flush(1.0)
            raise KafkaUnavailableError("delivery failed or timed out")

    def close(self) -> None:
        self._producer.flush(2.0)
