"""缓存、重试、熔断测试。

这三件东西直接决定 SerpAPI 250 次/月的额度能撑多久，行为必须可预期。
"""

from __future__ import annotations

import httpx
import pytest

from app.core.cache import TTLCache, make_key
from app.core.retry import CircuitBreaker, CircuitOpenError, retry_async


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestMakeKey:
    def test_key_is_stable_across_dict_ordering(self):
        assert make_key("ns", {"a": 1, "b": 2}) == make_key("ns", {"b": 2, "a": 1})

    def test_different_payloads_differ(self):
        assert make_key("ns", {"q": "北京"}) != make_key("ns", {"q": "上海"})

    def test_namespace_is_part_of_the_key(self):
        assert make_key("flights", {"q": "x"}) != make_key("hotels", {"q": "x"})

    def test_handles_non_json_types(self):
        from datetime import date

        assert make_key("ns", {"d": date(2026, 8, 10)})


class TestTTLCache:
    def test_set_then_get(self):
        c = TTLCache()
        c.set("k", {"v": 1}, ttl_s=60)
        assert c.get("k") == {"v": 1}

    def test_miss_returns_default(self):
        c = TTLCache()
        assert c.get("nope") is None
        assert c.get("nope", "fallback") == "fallback"

    def test_entry_expires(self):
        clock = FakeClock()
        c = TTLCache(clock=clock)
        c.set("k", "v", ttl_s=10)
        clock.advance(9)
        assert c.get("k") == "v"
        clock.advance(2)
        assert c.get("k") is None

    def test_zero_ttl_is_not_stored(self):
        c = TTLCache()
        c.set("k", "v", ttl_s=0)
        assert c.get("k") is None

    def test_lru_eviction_at_capacity(self):
        c = TTLCache(max_entries=2)
        c.set("a", 1, 60)
        c.set("b", 2, 60)
        c.get("a")  # a 变成最近使用
        c.set("c", 3, 60)  # 该淘汰 b
        assert c.get("a") == 1
        assert c.get("b") is None
        assert c.get("c") == 3
        assert c.stats.evictions == 1

    def test_stats_track_hit_ratio(self):
        c = TTLCache()
        c.set("k", "v", 60)
        c.get("k")
        c.get("k")
        c.get("missing")
        assert c.stats.hits == 2
        assert c.stats.misses == 1
        assert c.stats.hit_ratio == pytest.approx(2 / 3)

    def test_invalidate_and_clear(self):
        c = TTLCache()
        c.set("a", 1, 60)
        c.set("b", 2, 60)
        c.invalidate("a")
        assert c.get("a") is None
        assert len(c) == 1
        c.clear()
        assert len(c) == 0
        assert c.stats.hits == 0


class TestRetryAsync:
    async def test_returns_on_first_success(self):
        calls = []

        async def ok():
            calls.append(1)
            return "done"

        assert await retry_async(ok, delays=(0, 0)) == "done"
        assert len(calls) == 1

    async def test_retries_then_succeeds(self):
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise httpx.ConnectTimeout("boom")
            return "done"

        assert await retry_async(flaky, delays=(0, 0, 0)) == "done"
        assert len(calls) == 3

    async def test_gives_up_after_delays_are_exhausted(self):
        calls = []

        async def always_fails():
            calls.append(1)
            raise httpx.ConnectTimeout("boom")

        with pytest.raises(httpx.ConnectTimeout):
            await retry_async(always_fails, delays=(0, 0))
        assert len(calls) == 3  # 首次 + 2 次重试

    async def test_non_retriable_error_fails_immediately(self):
        calls = []

        async def bad_request():
            calls.append(1)
            raise ValueError("400 参数错误")

        with pytest.raises(ValueError):
            await retry_async(bad_request, delays=(0, 0), should_retry=lambda e: False)
        # 4xx 这类确定性错误重试只会白烧额度
        assert len(calls) == 1

    async def test_on_retry_callback_receives_attempt_and_wait(self):
        seen = []

        async def flaky():
            if len(seen) < 2:
                raise httpx.ConnectTimeout("boom")
            return "ok"

        await retry_async(
            flaky,
            delays=(0, 0, 0),
            on_retry=lambda n, exc, wait: seen.append((n, type(exc).__name__)),
        )
        assert seen == [(1, "ConnectTimeout"), (2, "ConnectTimeout")]


class TestCircuitBreaker:
    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("x", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.check()  # 不抛
        assert not cb.is_open

    def test_opens_at_threshold(self):
        cb = CircuitBreaker("serpapi", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open
        with pytest.raises(CircuitOpenError) as exc:
            cb.check()
        assert exc.value.name == "serpapi"

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("x", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.check()  # 计数已归零，不该开
        assert not cb.is_open

    def test_half_open_after_cooldown(self):
        clock = FakeClock()
        cb = CircuitBreaker("x", failure_threshold=2, reset_after_s=60, clock=clock)
        cb.record_failure()
        cb.record_failure()
        with pytest.raises(CircuitOpenError):
            cb.check()

        clock.advance(61)
        cb.check()  # half-open：放一个请求过去探路
        assert not cb.is_open

    def test_failure_in_half_open_reopens(self):
        clock = FakeClock()
        cb = CircuitBreaker("x", failure_threshold=2, reset_after_s=60, clock=clock)
        cb.record_failure()
        cb.record_failure()
        clock.advance(61)
        cb.check()
        cb.record_failure()  # 探路失败
        with pytest.raises(CircuitOpenError):
            cb.check()

    def test_retry_after_is_reported(self):
        clock = FakeClock()
        cb = CircuitBreaker("amap", failure_threshold=1, reset_after_s=60, clock=clock)
        cb.record_failure()
        clock.advance(20)
        with pytest.raises(CircuitOpenError) as exc:
            cb.check()
        assert exc.value.retry_after_s == pytest.approx(40)
