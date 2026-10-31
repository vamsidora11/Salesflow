import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Any redis-like client with awaitable incr(name) and expire(name, seconds).
CounterStore = Any


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter:
    """Per-key limiter backed by Redis counters.

    Two fixed windows: at most `burst` requests in any wall-clock second, and at most
    `rate * 10` in any 10-second window, so the sustained average is `rate` per second
    while short bursts up to `burst` are tolerated.
    """

    WINDOW_SECONDS = 10

    def __init__(
        self,
        store: CounterStore,
        rate: int,
        burst: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._rate = rate
        self._burst = burst
        self._clock = clock

    async def check(self, key: str) -> RateLimitResult:
        now = int(self._clock())
        second_key = f"rl:{key}:s:{now}"
        window = now // self.WINDOW_SECONDS
        window_key = f"rl:{key}:w:{window}"

        per_second = await self._store.incr(second_key)
        if per_second == 1:
            await self._store.expire(second_key, 2)
        per_window = await self._store.incr(window_key)
        if per_window == 1:
            await self._store.expire(window_key, self.WINDOW_SECONDS * 2)

        if per_second > self._burst:
            return RateLimitResult(False, 1)
        if per_window > self._rate * self.WINDOW_SECONDS:
            return RateLimitResult(False, (window + 1) * self.WINDOW_SECONDS - now)
        return RateLimitResult(True)
