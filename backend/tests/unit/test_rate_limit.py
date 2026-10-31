from app.services.rate_limit import RateLimiter
from tests.conftest import FakeRedis


async def test_burst_limit_within_one_second() -> None:
    limiter = RateLimiter(FakeRedis(), rate=50, burst=3, clock=lambda: 1000.0)
    results = [await limiter.check("k") for _ in range(5)]
    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert results[3].retry_after_seconds == 1


async def test_next_second_resets_burst() -> None:
    now = [1000.0]
    limiter = RateLimiter(FakeRedis(), rate=50, burst=2, clock=lambda: now[0])
    assert (await limiter.check("k")).allowed
    assert (await limiter.check("k")).allowed
    assert not (await limiter.check("k")).allowed
    now[0] = 1001.0
    assert (await limiter.check("k")).allowed


async def test_sustained_rate_is_capped_over_ten_second_window() -> None:
    now = [1000.0]
    limiter = RateLimiter(FakeRedis(), rate=2, burst=100, clock=lambda: now[0])  # 20 per window
    allowed = 0
    for second in range(10):
        now[0] = 1000.0 + second
        for _ in range(5):
            allowed += (await limiter.check("k")).allowed
    assert allowed == 20
    denied = await limiter.check("k")
    assert not denied.allowed
    assert 1 <= denied.retry_after_seconds <= 10


async def test_keys_are_independent() -> None:
    limiter = RateLimiter(FakeRedis(), rate=50, burst=1, clock=lambda: 5.0)
    assert (await limiter.check("a")).allowed
    assert not (await limiter.check("a")).allowed
    assert (await limiter.check("b")).allowed


async def test_counters_get_ttls_so_redis_does_not_grow() -> None:
    redis = FakeRedis()
    await RateLimiter(redis, rate=5, burst=5, clock=lambda: 7.0).check("k")
    assert len(redis.expiries) == 2
