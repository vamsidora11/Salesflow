"""Redis pub/sub: the consumer publishes after commit, every API instance fans out to sockets."""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis

from app.realtime.sequence import next_seq

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "analytics:"
CHANNEL_PATTERN = CHANNEL_PREFIX + "*"


def channel_for(tenant_id: uuid.UUID | str) -> str:
    return f"{CHANNEL_PREFIX}{tenant_id}"


def last_event_key(tenant_id: uuid.UUID | str) -> str:
    return f"analytics:last_event:{tenant_id}"


@dataclass(frozen=True)
class LiveUpdate:
    """New totals for one (store, hour bucket) plus what this batch added to them."""

    bucket: datetime
    store_id: uuid.UUID
    region: str
    revenue: Decimal
    units: int
    delta_revenue: Decimal
    delta_units: int


class LiveUpdatePublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, tenant_id: uuid.UUID, update: LiveUpdate) -> int:
        seq = await next_seq(self._redis, tenant_id)
        message = {
            "type": "update",
            "seq": seq,
            "bucket": update.bucket.isoformat(),
            "store_id": str(update.store_id),
            "region": update.region,
            "revenue": str(update.revenue),
            "units": update.units,
            "delta_revenue": str(update.delta_revenue),
            "delta_units": update.delta_units,
        }
        await self._redis.publish(channel_for(tenant_id), json.dumps(message))
        return seq

    async def mark_event_committed(self, tenant_id: uuid.UUID) -> None:
        await self._redis.set(last_event_key(tenant_id), str(time.time()), ex=7 * 24 * 3600)


class RedisFreshness:
    """data_freshness_seconds for analytics responses."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def age_seconds(self, tenant_id: uuid.UUID) -> float | None:
        try:
            raw = await self._redis.get(last_event_key(tenant_id))
        except Exception:
            logger.warning("freshness_lookup_failed")
            return None
        if raw is None:
            return None
        return max(0.0, round(time.time() - float(raw), 1))


class RedisFanout:
    """Subscribes to all tenant channels and hands messages to the connection manager.

    A pattern subscription keeps this simple; the manager drops messages for tenants that
    have no connected dashboards, and only ever delivers within the tenant of the channel.
    """

    def __init__(self, redis: Redis, manager: Any) -> None:
        self._redis = redis
        self._manager = manager

    async def run(self) -> None:
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.psubscribe(CHANNEL_PATTERN)
                async for item in pubsub.listen():
                    if item.get("type") != "pmessage":
                        continue
                    await self._dispatch(item["channel"], item["data"])
            except asyncio.CancelledError:
                await pubsub.aclose()
                raise
            except Exception:
                logger.warning("fanout_disconnected; retrying", exc_info=True)
                await asyncio.sleep(1.0)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def _dispatch(self, channel: bytes | str, data: bytes | str) -> None:
        name = channel.decode() if isinstance(channel, bytes) else channel
        tenant_id = name.removeprefix(CHANNEL_PREFIX)
        try:
            message = json.loads(data)
        except (TypeError, ValueError):
            logger.warning("fanout_bad_message")
            return
        await self._manager.broadcast(tenant_id, message)
