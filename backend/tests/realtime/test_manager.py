import asyncio
from typing import Any

import pytest

from app.realtime.websocket_manager import ClientStuckError, Connection, ConnectionManager


class FakeWs:
    def __init__(self, block: asyncio.Event | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._block = block

    async def send_json(self, data: Any) -> None:
        if self._block is not None:
            await self._block.wait()
        self.sent.append(data)


def update(
    seq: int, store: str = "s1", bucket: str = "2026-10-30T14:00:00+00:00", **kw: Any
) -> dict[str, Any]:
    return {
        "type": "update",
        "seq": seq,
        "bucket": bucket,
        "store_id": store,
        "region": kw.get("region", "EU"),
        "revenue": "100.00",
        "units": 10,
        "delta_revenue": kw.get("delta_revenue", "10.00"),
        "delta_units": kw.get("delta_units", 1),
    }


def conn(ws: FakeWs, tenant: str = "A", max_pending: int = 100, timeout: float = 1.0) -> Connection:
    return Connection(ws, tenant, max_pending, timeout)


async def drain(c: Connection, seconds: float = 0.05) -> None:
    task = asyncio.create_task(c.run_writer(heartbeat_seconds=60))
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_updates_are_delivered_in_seq_order() -> None:
    ws = FakeWs()
    c = conn(ws)
    c.offer(update(3, store="b"))
    c.offer(update(2, store="a"))
    await drain(c)
    assert [m["seq"] for m in ws.sent] == [2, 3]


async def test_same_bucket_updates_are_coalesced_keeping_latest_totals_and_summing_deltas() -> None:
    ws = FakeWs()
    c = conn(ws)
    c.offer(update(1, delta_revenue="10.00", delta_units=1))
    c.offer(update(2, delta_revenue="5.50", delta_units=2))
    c.offer(update(3, delta_revenue="1.00", delta_units=3))
    await drain(c)
    assert len(ws.sent) == 1
    assert ws.sent[0]["seq"] == 3
    assert ws.sent[0]["delta_revenue"] == "16.50"
    assert ws.sent[0]["delta_units"] == 6


async def test_store_and_region_filters() -> None:
    ws = FakeWs()
    c = conn(ws)
    c.subscribe("s1", None)
    c.offer(update(1, store="s1"))
    c.offer(update(2, store="s2"))
    c.subscribe(None, "APAC")
    c.offer(update(3, store="s3", region="APAC"))
    await drain(c)
    assert sorted(m["store_id"] for m in ws.sent) == ["s1", "s3"]
    c.unsubscribe("s1", None)
    c.unsubscribe(None, "APAC")
    assert c.store_ids == set() and c.regions == set()


async def test_overflow_drops_backlog_and_sends_resync_required() -> None:
    ws = FakeWs()
    c = conn(ws, max_pending=3)
    for i in range(10):
        c.offer(update(i, store=f"store-{i}"))  # distinct keys: cannot coalesce
    assert c.pending_count() == 0  # backlog dropped, memory bounded
    await drain(c)
    assert ws.sent == [{"type": "resync_required"}]


async def test_backlog_is_bounded_while_a_slow_client_blocks_sends() -> None:
    gate = asyncio.Event()
    ws = FakeWs(block=gate)
    c = conn(ws, max_pending=5, timeout=5.0)
    writer = asyncio.create_task(c.run_writer(60))
    c.offer(update(0, store="x"))
    await asyncio.sleep(0.02)  # writer is now stuck inside send_json
    for i in range(1, 200):
        c.offer(update(i, store=f"s{i}"))
        assert c.pending_count() <= 5
    gate.set()
    await asyncio.sleep(0.05)
    writer.cancel()
    assert {"type": "resync_required"} in ws.sent


async def test_a_completely_stuck_client_is_reported() -> None:
    ws = FakeWs(block=asyncio.Event())  # never unblocks
    c = conn(ws, timeout=0.05)
    c.offer(update(1))
    with pytest.raises(ClientStuckError):
        await asyncio.wait_for(c.run_writer(60), timeout=1.0)


async def test_heartbeat_is_sent_when_idle() -> None:
    ws = FakeWs()
    c = conn(ws)
    task = asyncio.create_task(c.run_writer(heartbeat_seconds=0.05))
    await asyncio.sleep(0.12)
    task.cancel()
    assert {"type": "heartbeat"} in ws.sent


async def test_non_update_messages_are_ignored() -> None:
    ws = FakeWs()
    c = conn(ws)
    c.offer({"type": "other"})
    assert c.pending_count() == 0


async def test_broadcast_never_crosses_tenants() -> None:
    manager = ConnectionManager()
    ws_a, ws_b = FakeWs(), FakeWs()
    a, b = conn(ws_a, "A"), conn(ws_b, "B")
    manager.add(a)
    manager.add(b)
    await manager.broadcast("A", update(1))
    assert a.pending_count() == 1
    assert b.pending_count() == 0
    assert manager.connection_count() == 2
    manager.remove(a)
    assert manager.connection_count("A") == 0
    await manager.broadcast("A", update(2))  # no connections: dropped quietly
    manager.remove(a)  # idempotent
