import uuid
from typing import Any


def sequence_key(tenant_id: uuid.UUID | str) -> str:
    return f"analytics:seq:{tenant_id}"


async def next_seq(store: Any, tenant_id: uuid.UUID | str) -> int:
    """Monotonically increasing per-tenant sequence number (Redis INCR)."""
    return int(await store.incr(sequence_key(tenant_id)))


class RedisSequenceSource:
    """Reads the current per-tenant sequence number (sent in `auth.ok`)."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def current(self, tenant_id: str) -> int:
        raw = await self._redis.get(sequence_key(tenant_id))
        return int(raw) if raw is not None else 0
