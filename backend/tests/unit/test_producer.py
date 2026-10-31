from typing import Any

import pytest

from app.streaming import producer as producer_module
from app.streaming.producer import KafkaEventPublisher, KafkaUnavailableError, OutboundMessage


class FakeProducer:
    behaviour = "ok"  # ok | delivery_error | buffer_full | timeout

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.callbacks: list[Any] = []
        self.produced: list[dict[str, Any]] = []
        self.purged = False

    def produce(self, topic: str, **kwargs: Any) -> None:
        if self.behaviour == "buffer_full":
            raise BufferError("queue full")
        self.produced.append({"topic": topic, **kwargs})
        self.callbacks.append(kwargs["on_delivery"])

    def poll(self, timeout: float) -> int:
        return 0

    def flush(self, timeout: float) -> int:
        if self.behaviour == "timeout":
            return len(self.callbacks)
        for cb in self.callbacks:
            cb("boom" if self.behaviour == "delivery_error" else None, None)
        self.callbacks = []
        return 0

    def purge(self) -> None:
        self.purged = True


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch) -> KafkaEventPublisher:
    FakeProducer.behaviour = "ok"
    monkeypatch.setattr(producer_module, "Producer", FakeProducer)
    return KafkaEventPublisher("localhost:9092", "sales.events.v1", 5.0)


def messages(n: int = 2) -> list[OutboundMessage]:
    return [
        OutboundMessage(key=f"store-{i}", value=b"{}", headers={"request-id": "r"})
        for i in range(n)
    ]


def fake(publisher: KafkaEventPublisher) -> FakeProducer:
    return publisher._producer  # type: ignore[no-any-return]


def test_producer_is_idempotent_acks_all_and_bounded(publisher: KafkaEventPublisher) -> None:
    config = fake(publisher).config
    assert config["acks"] == "all"
    assert config["enable.idempotence"] is True
    assert config["queue.buffering.max.messages"] <= 100_000
    assert config["message.timeout.ms"] == 5000


async def test_publish_sends_key_value_and_headers(publisher: KafkaEventPublisher) -> None:
    await publisher.publish(messages())
    sent = fake(publisher).produced
    assert [m["key"] for m in sent] == [b"store-0", b"store-1"]
    assert sent[0]["topic"] == "sales.events.v1"
    assert sent[0]["headers"] == [("request-id", b"r")]


@pytest.mark.parametrize("behaviour", ["delivery_error", "buffer_full", "timeout"])
async def test_failures_raise_unavailable(publisher: KafkaEventPublisher, behaviour: str) -> None:
    FakeProducer.behaviour = behaviour
    with pytest.raises(KafkaUnavailableError):
        await publisher.publish(messages())


async def test_undelivered_messages_are_purged_so_retries_do_not_duplicate(
    publisher: KafkaEventPublisher,
) -> None:
    FakeProducer.behaviour = "timeout"
    with pytest.raises(KafkaUnavailableError):
        await publisher.publish(messages())
    assert fake(publisher).purged
